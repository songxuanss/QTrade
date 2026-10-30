"""Recover the existing Linux loopback authentication deployment, without setup.

Only resume/restart crosses this interface. Endpoint discovery and process
ownership are local infrastructure concerns, never collection domain rules.
"""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

from dotenv import dotenv_values

NAMES = ("sso", "api", "worker")
PORTS = dict(zip(NAMES, (3121, 3122, 3123), strict=True))
DEPENDENCIES = ("postgres", "market-db", "object-store", "valkey")
# Child waits until ownership is durably recorded. EOF on a failed/crashed
# controller exits without executing the application or binding its port.
GATE = (
    "import os,sys; "
    "permit=sys.stdin.buffer.read(1); "
    "sys.exit(1) if permit!=b'1' else os.execvpe(sys.argv[1],sys.argv[1:],os.environ)"
)
ENV_KEYS = {
    "PATH",
    "LANG",
    "LC_ALL",
    "TZ",
    "HOME",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUNBUFFERED",
    "PYTHONNOUSERSITE",
    "LD_LIBRARY_PATH",
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "DATABASE_URL",
    "MARKET_DATABASE_URL",
    "REDIS_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
}
ENV_PREFIXES = ("QTRADE_", "OBJECT_STORE_", "MAIRUI_", "COLLECTOR_", "DATA_COLLECTION_")


class RecoveryError(RuntimeError):
    """Fixed public error codes only; never format private config or exceptions."""


def _private(path: Path, value: Any) -> None:
    body = json.dumps(value).encode()
    if len(body) > 4 * 1024 * 1024:
        raise RecoveryError("state_too_large")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
        with _directory(path.parent) as descriptor:
            os.fsync(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _read(path: Path, maximum: int = 4 * 1024 * 1024) -> Any:
    with path.open("rb") as source:
        body = source.read(maximum + 1)
    if len(body) > maximum:
        raise RecoveryError("state_too_large")
    return json.loads(body)


def _environment(values: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in values.items()
        if key in ENV_KEYS or key.startswith(ENV_PREFIXES)
    }


def _rehost(value: str, host: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"} or "@" not in parsed.netloc:
        raise RecoveryError("invalid_database_profile")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc.rsplit("@", 1)[0] + "@" + host + ":5432",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def _command(root: Path, name: str) -> list[str]:
    if name == "sso":
        return [str(root / ".tools/sso-venv/bin/python"), "scripts/sso-local.py", "serve"]
    return [
        str(root / ".venv/bin/python"),
        "-m",
        "uvicorn",
        f"qtrade_{name}.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(PORTS[name]),
        "--no-access-log",
    ]


@dataclass
class Pending:
    record: dict[str, Any]
    process: subprocess.Popen[bytes]

    def release(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(b"1")
        self.process.stdin.close()

    def abort(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()


class LocalHost:
    """OS adapter, private seam for controlled failure tests of the same facade."""

    def __init__(self, root: Path):
        self.root = root

    def process(self, pid: int) -> tuple[str, list[str]] | None:
        try:
            suffix = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()
            if suffix[0] == "Z":
                return None
            return suffix[19], suffix
        except FileNotFoundError:
            return None

    def capture(self, name: str, record: dict[str, Any]) -> dict[str, str] | None:
        info = self.process(record["pid"])
        if info is None or info[0] != record["identity"]:
            return None
        directory = Path("/proc", str(record["pid"]))
        command = directory.joinpath("cmdline").read_bytes().decode().rstrip("\0").split("\0")
        expected = _command(self.root, name)
        gated = [expected[0], "-c", GATE, *expected]
        if (
            command not in [expected, gated]
            or Path(os.readlink(directory / "cwd")) != self.root
            or info[1][2] != str(record["pid"])
            or info[1][3] != str(record["pid"])
        ):
            raise RecoveryError("process_ownership_mismatch")
        values = dict(
            item.split("=", 1)
            for item in directory.joinpath("environ").read_bytes().decode().split("\0")
            if "=" in item
        )
        # Recheck after reading /proc so PID reuse cannot supply another
        # process's startup configuration or authorize its termination.
        later = self.process(record["pid"])
        if later is None or later[0] != record["identity"]:
            return None
        return _environment(values)

    def _container(self, project: str, service: str) -> str:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                f"label=com.docker.compose.service={service}",
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        ids = result.stdout.split()
        if len(ids) != 1:
            raise RecoveryError("dependency_not_found")
        return ids[0]

    def _container_ready(self, project: str, service: str) -> tuple[dict[str, Any], bool]:
        container = self._container(project, service)
        started = False
        for attempt in range(100):
            info = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
            labels = info["Config"]["Labels"]
            directory = labels.get("com.docker.compose.project.working_dir", "")
            if (
                labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.service") != service
                or Path(directory).resolve() != self.root
            ):
                raise RecoveryError("dependency_identity_mismatch")
            if info["State"]["Status"] in {"created", "exited"} and attempt == 0:
                subprocess.run(["docker", "start", container], capture_output=True, check=True)
                started = True
            elif info["State"]["Status"] == "running":
                if info["State"].get("Health", {}).get("Status", "healthy") == "healthy":
                    return info, started
            time.sleep(0.25)
        raise RecoveryError("dependency_not_ready")

    def dependency(self, service: str) -> tuple[str, dict[str, str]]:
        info, _ = self._container_ready("qtrade", service)
        network = info["NetworkSettings"]["Networks"].get("qtrade_default")
        if not network:
            raise RecoveryError("dependency_network_missing")
        host = str(ipaddress.IPv4Address(network["IPAddress"]))
        values = dict(item.split("=", 1) for item in info["Config"]["Env"])
        return host, values

    def applications(self) -> list[dict[str, str]]:
        results = []
        for project, service in (
            ("qtrade-collector", "collector-db"),
            ("qtrade-collector", "collector"),
            ("qtrade", "web"),
        ):
            _, started = self._container_ready(project, service)
            results.append({"service": service, "action": "started" if started else "kept"})
        return results

    def free_port(self, name: str) -> bool:
        try:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", PORTS[name]))
            return True
        except OSError:
            return False

    def stop(self, name: str, record: dict[str, Any]) -> None:
        if self.capture(name, record) is None:
            return
        os.killpg(record["pid"], signal.SIGTERM)
        until = time.monotonic() + 10
        while (info := self.process(record["pid"])) and info[0] == record["identity"]:
            if time.monotonic() >= until:
                os.killpg(record["pid"], signal.SIGKILL)
                # SIGKILL delivery is asynchronous too. Observe this identity's
                # exit before allowing the next application to bind its port.
                until = time.monotonic() + 5
                while (info := self.process(record["pid"])) and info[0] == record["identity"]:
                    if time.monotonic() >= until:
                        raise RecoveryError("process_not_stopped")
                    time.sleep(0.1)
                return
            time.sleep(0.1)

    def launch(self, name: str, environment: dict[str, str]) -> Pending:
        command = _command(self.root, name)
        path = self.root / ".tools/verification-local" / f"{name}.log"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "ab") as output:
            os.fchmod(output.fileno(), 0o600)
            child = subprocess.Popen(
                [command[0], "-c", GATE, *command],
                cwd=self.root,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        info = self.process(child.pid)
        if info is None:
            raise RecoveryError("process_start_failed")
        return Pending(dict(name=name, pid=child.pid, identity=info[0]), child)

    def ready(self, name: str, record: dict[str, Any]) -> None:
        path = "healthz" if name == "sso" else "ready"
        until = time.monotonic() + 30
        local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < until:
            info = self.process(record["pid"])
            if info is None or info[0] != record["identity"]:
                raise RecoveryError("process_exited")
            try:
                with local_http.open(f"http://127.0.0.1:{PORTS[name]}/{path}", timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                pass
            time.sleep(0.2)
        # Keep this exact process registered. A timeout is not a termination.
        raise RecoveryError("process_not_ready")


def recover(
    root: Path, *, force: bool = False, host: LocalHost | None = None
) -> list[dict[str, str]]:
    """Resume saved native SSO/API/Worker; force explicitly restarts owned ones.

    Requires existing private SSO state and Compose dependencies. No accounts,
    databases, buckets, keys or images are created/rebuilt. Configuration and
    child ownership are durable before an application is allowed to execute.
    """
    root = root.resolve()
    folder = root / ".tools/verification-local"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    host = host or LocalHost(root)
    try:
        descriptor = os.open(
            folder / "recovery.lock", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "a") as lock:
            os.fchmod(lock.fileno(), 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RecoveryError("recovery_in_progress") from None
            return _recover(root, folder, host, force)
    except RecoveryError:
        raise
    except Exception:
        raise RecoveryError("recovery_failed") from None


def _recover(root: Path, folder: Path, host: LocalHost, force: bool) -> list[dict[str, str]]:
    state_path = root / ".tools/sso-local/state.json"
    state = _read(state_path)
    records_path = folder / "processes.json"
    records = _read(records_path) if records_path.exists() else []
    journal_path = folder / "startup.json"
    journal: dict[str, Any] = (
        _read(journal_path)
        if journal_path.exists()
        else {"version": 1, "root": str(root), "services": {}}
    )
    if (
        type(journal.get("version")) is not int
        or journal["version"] != 1
        or journal.get("root") != str(root)
        or not isinstance(journal.get("services"), dict)
    ):
        raise RecoveryError("invalid_startup_state")
    indexed = {r["name"]: r for r in records}
    if len(indexed) != len(records):
        raise RecoveryError("invalid_process_state")
    live = {}
    for name in NAMES:
        record = indexed.get(name)
        if record is not None:
            if type(record.get("pid")) is not int or record["pid"] <= 1:
                raise RecoveryError("invalid_process_state")
            live[name] = host.capture(name, record)
        else:
            live[name] = None
        if live[name] is None and not host.free_port(name):
            raise RecoveryError("port_in_use")
    # Initial adoption uses existing private configuration, never prepare().
    base = _environment(dict(os.environ))
    for file in (".env", ".env.local-auth"):
        base.update(
            _environment({k: v for k, v in dotenv_values(root / file).items() if v is not None})
        )
    private = dotenv_values(root / ".env.collector")
    base.update(
        PYTHONPATH=os.pathsep.join(
            str(p / "src")
            for group in ("apps", "packages", "adapters")
            for p in (root / group).iterdir()
            if (p / "src").is_dir()
        ),
        QTRADE_ENV="local",
        QTRADE_LIVE_TRADING_ENABLED="false",
        QTRADE_PUBLIC_ORIGIN="http://127.0.0.1:13000",
        QTRADE_INSECURE_LOCAL_COOKIES="true",
        QTRADE_OIDC_ALLOW_HTTP="true",
        QTRADE_OIDC_ISSUER=state["issuer"],
        QTRADE_OIDC_CLIENT_ID=state["client_id"],
        QTRADE_OIDC_CLIENT_SECRET=state["client_secret"],
        QTRADE_OIDC_TRANSACTION_KEY=state["transaction_key"],
        QTRADE_BFF_SECRET=state["bridge_secret"],
        QTRADE_SESSION_PRIMARY_KEY="verification",
        QTRADE_SESSION_KEYS_JSON=json.dumps({"verification": state["session_key"]}),
        QTRADE_COLLECTOR_URL="http://127.0.0.1:18100",
        QTRADE_COLLECTOR_ALLOW_HTTP="true",
    )
    if private.get("COLLECTOR_SERVICE_TOKEN"):
        base["COLLECTOR_SERVICE_TOKEN"] = str(private["COLLECTOR_SERVICE_TOKEN"])
    environments = {
        name: _environment({**base, **journal["services"].get(name, {}), **(live[name] or {})})
        for name in NAMES
    }
    for environment in environments.values():
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in environment.items()):
            raise RecoveryError("invalid_startup_environment")
        if (
            environment.get("QTRADE_ENV") != "local"
            or environment.get("QTRADE_LIVE_TRADING_ENABLED") != "false"
        ):
            raise RecoveryError("not_local_profile")
        if not environment.get("COLLECTOR_SERVICE_TOKEN"):
            raise RecoveryError("collector_configuration_missing")
    dependencies = {service: host.dependency(service) for service in DEPENDENCIES}
    pg, _ = dependencies["postgres"]
    state_next = dict(state)
    for key in ("api_url", "sso_url", "prefix"):
        state_next[key] = _rehost(state[key], pg)
    endpoint_changed = any(
        state[key] != state_next[key] for key in ("api_url", "sso_url", "prefix")
    )
    market, market_values = dependencies["market-db"]
    objects, object_values = dependencies["object-store"]
    cache, _ = dependencies["valkey"]
    restart = set()
    for name, environment in environments.items():
        before = dict(environment)
        # Only hosts change for existing private DB references. Identity keys,
        # database names and object bucket remain the original configuration.
        environment.update(
            DATABASE_URL=_rehost(environment.get("DATABASE_URL", state["api_url"]), pg),
            MARKET_DATABASE_URL=_rehost(environment["MARKET_DATABASE_URL"], market)
            if environment.get("MARKET_DATABASE_URL")
            else (
                f"postgresql://{quote(market_values['POSTGRES_USER'], safe='')}:"
                f"{quote(market_values['POSTGRES_PASSWORD'], safe='')}@{market}:5432/"
                f"{market_values['POSTGRES_DB']}"
            ),
            OBJECT_STORE_URL=f"http://{objects}:9000",
            OBJECT_STORE_BUCKET=environment.get("OBJECT_STORE_BUCKET", "qtrade-verification"),
            OBJECT_STORE_ACCESS_KEY=environment.get(
                "OBJECT_STORE_ACCESS_KEY", object_values["MINIO_ROOT_USER"]
            ),
            OBJECT_STORE_SECRET_KEY=environment.get(
                "OBJECT_STORE_SECRET_KEY", object_values["MINIO_ROOT_PASSWORD"]
            ),
            REDIS_URL=f"redis://{cache}:6379/0",
        )
        if before != environment:
            endpoint_changed = True
        if force or live[name] is None:
            restart.add(name)
    if endpoint_changed:
        restart.update(NAMES)
    _private(journal_path, {"version": 1, "root": str(root), "services": environments})
    _private(state_path, state_next)
    for name in NAMES:
        if name in restart and live[name] is not None:
            host.stop(name, indexed[name])
    output = []
    for name in NAMES:
        if name in restart:
            if not host.free_port(name):
                raise RecoveryError("port_in_use")
            pending = host.launch(name, environments[name])
            try:
                indexed[name] = pending.record
                _private(records_path, list(indexed.values()))
                pending.release()
            finally:
                pending.abort()
            output.append({"service": name, "action": "started"})
        else:
            output.append({"service": name, "action": "kept"})
        host.ready(name, indexed[name])
    return output + host.applications()
