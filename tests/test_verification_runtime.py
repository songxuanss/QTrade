"""Controlled OS failures across the real recovery facade; not live UI proof."""

import fcntl
import os
import sys

import pytest
from scripts import verification_runtime as runtime


@pytest.fixture
def profile(tmp_path):
    state = {
        "prefix": "postgresql://original:private-db@10.42.0.1:5432/",
        "api_url": "postgresql://original:private-db@10.42.0.1:5432/original_api",
        "sso_url": "postgresql+psycopg://original:private-db@10.42.0.1:5432/original_sso",
        "issuer": "http://127.0.0.1:3121",
        "client_id": "original-client",
        "client_secret": "private-client",
        "transaction_key": "private-transaction",
        "bridge_secret": "private-bridge",
        "session_key": "private-session",
        "password": "private-password",
        "databases": ["original_sso", "original_api"],
    }
    for name in ("apps", "packages", "adapters"):
        (tmp_path / name).mkdir()
    runtime._private(tmp_path / ".tools/sso-local/state.json", state)
    (tmp_path / ".env.collector").write_text("COLLECTOR_SERVICE_TOKEN=private-service\n")
    return tmp_path, state


class FakePending:
    def __init__(self, host, name, environment):
        self.host, self.name, self.environment = host, name, environment
        host.sequence += 1
        self.record = dict(name=name, pid=host.sequence, identity=str(host.sequence))
        self.released = self.aborted = False

    def release(self):
        records = runtime._read(self.host.root / ".tools/verification-local/processes.json")
        assert self.record in records  # Ownership is durable before execution.
        startup = runtime._read(self.host.root / ".tools/verification-local/startup.json")
        assert startup["services"][self.name] == self.environment
        self.released = True
        self.host.live[self.name] = self.record, dict(self.environment)

    def abort(self):
        self.aborted = True


class FakeHost(runtime.LocalHost):
    def __init__(self, root):
        super().__init__(root)
        self.sequence = 100
        self.live, self.started, self.stopped = {}, [], []
        self.blocked = set()
        self.bad_owner = None
        self.not_ready = None
        self.address = "10.42.0.2"
        self.dependency_calls = self.application_calls = 0

    def capture(self, name, record):
        if name == self.bad_owner:
            raise runtime.RecoveryError("process_ownership_mismatch")
        saved = self.live.get(name)
        return dict(saved[1]) if saved and saved[0] == record else None

    def free_port(self, name):
        return name not in self.blocked and name not in self.live

    def dependency(self, name):
        self.dependency_calls += 1
        return self.address, dict(
            POSTGRES_USER="original",
            POSTGRES_PASSWORD="private-db",
            POSTGRES_DB="original_market",
            MINIO_ROOT_USER="original",
            MINIO_ROOT_PASSWORD="private-store",
        )

    def stop(self, name, record):
        assert self.capture(name, record) is not None
        # Configuration already survived the controller before stopping anyone.
        assert (self.root / ".tools/verification-local/startup.json").exists()
        self.stopped.append(name)
        del self.live[name]

    def launch(self, name, environment):
        pending = FakePending(self, name, environment)
        self.started.append(pending)
        return pending

    def ready(self, name, record):
        assert self.live[name][0] == record
        if self.not_ready == name:
            raise runtime.RecoveryError("process_not_ready")

    def applications(self):
        self.application_calls += 1
        return [{"service": "web", "action": "kept"}]


def test_resume_and_repeat_preserve_identity_and_environment_without_setup(profile, monkeypatch):
    root, original = profile
    host = FakeHost(root)
    monkeypatch.setenv("UNRELATED_TOOL_TOKEN", "do-not-persist")
    result = runtime.recover(root, host=host)
    assert [r["action"] for r in result[:3]] == ["started"] * 3
    state = runtime._read(root / ".tools/sso-local/state.json")
    for key in original.keys() - {"api_url", "sso_url", "prefix"}:
        assert state[key] == original[key]
    assert state["api_url"].endswith("@10.42.0.2:5432/original_api")
    startup_path = root / ".tools/verification-local/startup.json"
    assert startup_path.stat().st_mode & 0o777 == 0o600
    assert "do-not-persist" not in startup_path.read_text()
    host.live["api"][1]["QTRADE_CUSTOM_LIMIT"] = "17"
    # A live setting absent from env files is retained, including after exit.
    again = runtime.recover(root, host=host)
    assert [r["action"] for r in again[:3]] == ["kept"] * 3
    assert len(host.started) == 3 and not host.stopped
    del host.live["api"]
    restored = runtime.recover(root, host=host)
    assert restored[1]["action"] == "started" and len(host.started) == 4
    assert host.live["api"][1]["QTRADE_CUSTOM_LIMIT"] == "17"


def test_changed_addresses_restart_only_owned_native_processes(profile):
    root, _ = profile
    host = FakeHost(root)
    runtime.recover(root, host=host)
    saved = {n: dict(e) for n, (_, e) in host.live.items()}
    host.address = "10.42.0.3"
    runtime.recover(root, host=host)
    assert host.stopped == list(runtime.NAMES) and len(host.started) == 6
    for name, (_, environment) in host.live.items():
        assert "@10.42.0.3:" in environment["DATABASE_URL"]
        assert environment["OBJECT_STORE_URL"] == "http://10.42.0.3:9000"
        for key in ("QTRADE_SESSION_KEYS_JSON", "QTRADE_OIDC_CLIENT_SECRET", "OBJECT_STORE_BUCKET"):
            assert environment[key] == saved[name][key]


def test_readiness_timeout_keeps_same_owned_process_for_next_resume(profile):
    root, _ = profile
    host = FakeHost(root)
    host.not_ready = "api"
    with pytest.raises(runtime.RecoveryError, match="process_not_ready"):
        runtime.recover(root, host=host)
    api = host.live["api"][0]
    assert len(host.started) == 2
    host.not_ready = None
    runtime.recover(root, host=host)
    assert host.live["api"][0] == api and len(host.started) == 3
    assert not host.stopped


def test_failed_ownership_write_never_releases_unregistered_child(profile, monkeypatch):
    root, _ = profile
    host = FakeHost(root)
    writer = runtime._private

    def fail(path, value):
        if path.name == "processes.json":
            raise OSError("private-do-not-print")
        writer(path, value)

    monkeypatch.setattr(runtime, "_private", fail)
    with pytest.raises(runtime.RecoveryError) as error:
        runtime.recover(root, host=host)
    assert str(error.value) == "recovery_failed"
    assert not host.started[0].released and host.started[0].aborted
    assert not host.live and host.application_calls == 0
    monkeypatch.setattr(runtime, "_private", writer)
    runtime.recover(root, host=host)
    assert set(host.live) == set(runtime.NAMES)


@pytest.mark.parametrize("reason", ["port", "owner", "profile"])
def test_preflight_refuses_without_stopping_foreign_processes(profile, reason):
    root, _ = profile
    host = FakeHost(root)
    if reason == "port":
        host.blocked.add("api")
    else:
        runtime.recover(root, host=host)
        host.started.clear()
        if reason == "owner":
            host.bad_owner = "api"
        else:
            host.live["api"][1]["QTRADE_LIVE_TRADING_ENABLED"] = "true"
    with pytest.raises(runtime.RecoveryError):
        runtime.recover(root, host=host, force=True)
    assert not host.started and not host.stopped


def test_concurrent_recovery_is_rejected_before_mutation(profile):
    root, _ = profile
    folder = root / ".tools/verification-local"
    folder.mkdir()
    with (folder / "recovery.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(runtime.RecoveryError, match="recovery_in_progress"):
            runtime.recover(root, host=FakeHost(root))
    assert not (folder / "startup.json").exists()


def test_real_gate_aborts_on_controller_loss_and_executes_only_after_release(tmp_path):
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").symlink_to(sys.executable)
    (tmp_path / ".tools/verification-local").mkdir(parents=True)
    modules = tmp_path / "fake_modules"
    modules.mkdir()
    (modules / "uvicorn.py").write_text(
        "from pathlib import Path\nPath(__file__).with_name('executed').touch()\n"
    )
    environment = {"PATH": os.environ["PATH"], "PYTHONPATH": str(modules)}
    host = runtime.LocalHost(tmp_path)
    first = host.launch("api", environment)
    assert host.capture("api", first.record) is not None
    assert not (modules / "executed").exists()
    first.abort()
    assert first.process.wait(timeout=5) != 0 and not (modules / "executed").exists()
    second = host.launch("api", environment)
    runtime._private(tmp_path / ".tools/verification-local/processes.json", [second.record])
    second.release()
    assert second.process.wait(timeout=5) == 0 and (modules / "executed").exists()


def test_current_python_process_cannot_be_adopted_as_api(tmp_path):
    host = runtime.LocalHost(tmp_path)
    identity = host.process(os.getpid())[0]
    with pytest.raises(runtime.RecoveryError, match="process_ownership_mismatch"):
        host.capture("api", dict(pid=os.getpid(), identity=identity))


def test_all_native_processes_can_resume_from_journal_after_env_file_loss(profile):
    root, state = profile
    host = FakeHost(root)
    runtime.recover(root, host=host)
    saved = {name: dict(env) for name, (_, env) in host.live.items()}
    host.live.clear()
    (root / ".env.collector").unlink()
    runtime.recover(root, host=host)
    assert len(host.started) == 6 and not host.stopped
    for name, (_, env) in host.live.items():
        assert env == saved[name]
    actual = runtime._read(root / ".tools/sso-local/state.json")
    assert actual["session_key"] == state["session_key"]


def test_state_rehost_preserves_credentials_database_and_query_exactly():
    value = "postgresql+psycopg://u%40name:p%2F10.42.0.1@10.42.0.1:15432/db_10.42.0.1?application_name=10.42.0.1"
    changed = runtime._rehost(value, "10.42.0.2")
    assert (
        changed
        == "postgresql+psycopg://u%40name:p%2F10.42.0.1@10.42.0.2:5432/db_10.42.0.1?application_name=10.42.0.1"
    )


def test_corrupted_journal_is_rejected_before_stopping_existing_apps(profile):
    root, _ = profile
    host = FakeHost(root)
    runtime.recover(root, host=host)
    host.started.clear()
    journal = root / ".tools/verification-local/startup.json"
    journal.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(runtime.RecoveryError, match="state_too_large"):
        runtime.recover(root, host=host, force=True)
    assert not host.started and not host.stopped


def test_forced_stop_observes_sigkill_exit_before_return(tmp_path, monkeypatch):
    host = runtime.LocalHost(tmp_path)
    record = {"pid": 12345, "identity": "original"}
    monkeypatch.setattr(host, "capture", lambda *args: {})
    observations = iter([("original", []), ("original", []), None])
    observed = []

    def process(pid):
        observed.append(pid)
        return next(observations)

    monkeypatch.setattr(host, "process", process)
    ticks = iter([0, 11, 11, 12])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(runtime.time, "sleep", lambda duration: None)
    sent = []
    monkeypatch.setattr(runtime.os, "killpg", lambda pid, sig: sent.append((pid, sig)))
    host.stop("api", record)
    assert sent == [(12345, runtime.signal.SIGTERM), (12345, runtime.signal.SIGKILL)]
    assert len(observed) == 3


def test_explicit_restart_replaces_owned_processes_with_same_saved_configuration(profile):
    root, _ = profile
    host = FakeHost(root)
    runtime.recover(root, host=host)
    previous = {name: dict(env) for name, (_, env) in host.live.items()}
    result = runtime.recover(root, host=host, force=True)
    assert [item["action"] for item in result[:3]] == ["started"] * 3
    assert host.stopped == list(runtime.NAMES)
    assert {name: env for name, (_, env) in host.live.items()} == previous
