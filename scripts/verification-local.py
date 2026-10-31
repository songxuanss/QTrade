"""Manage local verification services; resume retains the existing identity and data."""

import argparse
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / ".tools/verification-local"
PROCESS_STATE = DIRECTORY / "processes.json"
SSO_STATE = ROOT / ".tools/sso-local/state.json"


def container_environment(service):
    container = subprocess.check_output(
        ["docker", "compose", "ps", "-q", service], cwd=ROOT, text=True
    ).strip()
    if not container:
        raise RuntimeError(f"Compose {service} must be running")
    info = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
    address = next(iter(info["NetworkSettings"]["Networks"].values()))["IPAddress"]
    values = dict(value.split("=", 1) for value in info["Config"]["Env"])
    return address, values


def identity(pid):
    try:
        suffix = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()
        return None if suffix[0] == "Z" else suffix[19]
    except FileNotFoundError:
        return None


def start():
    DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    if PROCESS_STATE.exists():
        raise RuntimeError("deployment already recorded; use status or stop first")
    for port in (3120, 3121, 3122, 3123):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    if not SSO_STATE.exists():
        subprocess.run(
            [str(ROOT / ".tools/sso-venv/bin/python"), "scripts/sso-local.py", "prepare"],
            cwd=ROOT,
            check=True,
        )
    state = json.loads(SSO_STATE.read_text())
    sources = [
        str(path / "src")
        for group in ("apps", "packages", "adapters")
        for path in (ROOT / group).iterdir()
        if (path / "src").is_dir()
    ]
    environment = {
        **{key: value for key, value in dotenv_values(ROOT / ".env").items() if value is not None},
        **os.environ,
        "PYTHONPATH": os.pathsep.join(sources),
        "QTRADE_ENV": "local",
        "QTRADE_LIVE_TRADING_ENABLED": "false",
        "QTRADE_INSECURE_LOCAL_COOKIES": "true",
        "QTRADE_OIDC_ALLOW_HTTP": "true",
        "QTRADE_OIDC_ISSUER": state["issuer"],
        "QTRADE_OIDC_CLIENT_ID": state["client_id"],
        "QTRADE_OIDC_CLIENT_SECRET": state["client_secret"],
        "QTRADE_OIDC_TRANSACTION_KEY": state["transaction_key"],
        "QTRADE_BFF_SECRET": state["bridge_secret"],
        "QTRADE_SESSION_PRIMARY_KEY": "verification",
        "QTRADE_SESSION_KEYS_JSON": json.dumps({"verification": state["session_key"]}),
        "QTRADE_PUBLIC_ORIGIN": "http://127.0.0.1:3120",
        "QTRADE_API_ORIGIN": "http://127.0.0.1:3122",
        "QTRADE_API_PUBLIC_ORIGIN": "http://127.0.0.1:3122",
        "DATABASE_URL": state["api_url"],
        "PORT": "3120",
        "HOSTNAME": "127.0.0.1",
    }
    objects, object_values = container_environment("object-store")
    market, market_values = container_environment("market-db")
    cache, _ = container_environment("valkey")
    # User shells may inherit a Windows NO_PROXY with wildcard/CIDR forms that
    # HTTP clients do not reliably honor. Add the concrete dependency addresses.
    bypass = ",".join(
        dict.fromkeys(
            ["127.0.0.1", "localhost", "::1", objects, market, cache]
        )
    )
    environment["NO_PROXY"] = environment["no_proxy"] = bypass
    environment.update(
        OBJECT_STORE_URL=f"http://{objects}:9000",
        OBJECT_STORE_BUCKET="qtrade-verification",
        OBJECT_STORE_ACCESS_KEY=object_values["MINIO_ROOT_USER"],
        OBJECT_STORE_SECRET_KEY=object_values["MINIO_ROOT_PASSWORD"],
        MARKET_DATABASE_URL=(
            f"postgresql://{market_values['POSTGRES_USER']}:"
            f"{market_values['POSTGRES_PASSWORD']}@{market}:5432/"
            f"{market_values['POSTGRES_DB']}"
        ),
        REDIS_URL=f"redis://{cache}:6379/0",
    )
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "qtrade_postgres.migrate"],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "qtrade_postgres.market_migrate"],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "qtrade_data_postgres.migrate"],
        cwd=ROOT,
        env={
            **environment,
            "DATA_COLLECTION_DATABASE_URL": environment.get(
                "DATA_COLLECTION_DATABASE_URL", environment["DATABASE_URL"]
            ),
        },
        check=True,
    )
    login = DIRECTORY / "LOGIN.txt"
    login.write_text(
        "QTrade 本地验收环境（真实 SSO，独立数据库，实盘关闭）\n"
        "网址：http://127.0.0.1:3120/workspace/strategies\n"
        "邮箱：qtrade-integration@example.test\n"
        f"密码：{state['password']}\n"
    )
    login.chmod(0o600)
    processes = []
    commands = (
        ("sso", ROOT, [str(ROOT / ".tools/sso-venv/bin/python"), "scripts/sso-local.py", "serve"]),
        (
            "api",
            ROOT,
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "qtrade_api.main:app",
                "--host",
                environment.get("QTRADE_VERIFICATION_API_BIND", "127.0.0.1"),
                "--port",
                "3122",
                "--no-access-log",
            ],
        ),
        (
            "worker",
            ROOT,
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "qtrade_worker.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "3123",
                "--no-access-log",
            ],
        ),
        ("web", ROOT / "apps/web", ["node", "tests/serve-production.mjs"]),
    )
    try:
        for name, cwd, command in commands:
            log_path = DIRECTORY / f"{name}.log"
            with log_path.open("a") as log:
                log_path.chmod(0o600)
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            processes.append({"name": name, "pid": process.pid, "identity": identity(process.pid)})
        PROCESS_STATE.write_text(json.dumps(processes))
        PROCESS_STATE.chmod(0o600)
        for url in (
            "http://127.0.0.1:3121/healthz",
            "http://127.0.0.1:3122/ready",
            "http://127.0.0.1:3123/ready",
            "http://127.0.0.1:3120/health",
        ):
            # Mounted Windows filesystems can make Python startup substantially
            # slower than local disk; readiness is a bounded wait, not a health proof.
            deadline = time.monotonic() + int(
                os.environ.get("QTRADE_VERIFICATION_READINESS_SECONDS", "120")
            )
            while True:
                try:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"deployment readiness failed: {url}") from None
                    time.sleep(0.5)
    except Exception:
        for process in processes:
            if identity(process["pid"]) == process["identity"]:
                os.killpg(process["pid"], signal.SIGTERM)
        PROCESS_STATE.unlink(missing_ok=True)
        raise
    print("Ready: http://127.0.0.1:3120/workspace/strategies")
    print("Login instructions: .tools/verification-local/LOGIN.txt")


def status():
    if not PROCESS_STATE.exists():
        print("Verification deployment is not running")
        return
    for process in json.loads(PROCESS_STATE.read_text()):
        alive = identity(process["pid"]) == process["identity"]
        print(f"{process['name']}: {'running' if alive else 'stopped'}")


def stop():
    if PROCESS_STATE.exists():
        processes = json.loads(PROCESS_STATE.read_text())
        for process in processes:
            if identity(process["pid"]) == process["identity"]:
                os.killpg(process["pid"], signal.SIGTERM)
        deadline = time.monotonic() + 10
        while any(identity(p["pid"]) == p["identity"] for p in processes):
            if time.monotonic() >= deadline:
                for process in processes:
                    if identity(process["pid"]) == process["identity"]:
                        os.killpg(process["pid"], signal.SIGKILL)
                break
            time.sleep(0.1)
        PROCESS_STATE.unlink()
    print("Verification services stopped; databases and login account retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop", "resume", "restart"))
    arguments = parser.parse_args()
    if arguments.action in {"resume", "restart"}:
        from verification_runtime import RecoveryError, recover

        try:
            print(json.dumps(recover(ROOT, force=arguments.action == "restart")))
        except RecoveryError as error:
            raise SystemExit("Local recovery incomplete: " + str(error)) from None
    else:
        {"start": start, "status": status, "stop": stop}[arguments.action]()
