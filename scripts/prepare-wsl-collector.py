"""Prepare private configuration for the dedicated Ubuntu 26 collector.

Reuses its own generated identity on retries. Existing provider configuration is
preserved, and unknown collector credentials are never silently replaced. This
does not recover the old Docker environment's provider licence or business data.
"""

import json
import os
import secrets
import tempfile
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".tools/windows-environment/collector-state.json"


def private_write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_env(path: Path, values: dict[str, str]) -> None:
    if any("\n" in value or "\r" in value or "\0" in value for value in values.values()):
        raise SystemExit("Private configuration contains an unsupported multiline value")
    private_write(
        path, "".join(f"{key}={json.dumps(value)}\n" for key, value in sorted(values.items()))
    )


def main() -> None:
    if os.environ.get("WSL_DISTRO_NAME") != "QTrade-Ubuntu-26" or os.geteuid() == 0:
        raise SystemExit("Run as the qtrade user inside QTrade-Ubuntu-26")
    import fcntl

    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE.parent / "collector-prepare.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another collector configuration preparation is running") from None
        prepare()


def prepare() -> None:
    native_path, collector_path = ROOT / ".env", ROOT / ".env.collector"
    native = {key: value for key, value in dotenv_values(native_path).items() if value is not None}
    collector = {
        key: value for key, value in dotenv_values(collector_path).items() if value is not None
    }
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if (
            not isinstance(state, dict)
            or set(state) != {"service_token", "db_password"}
            or any(
                not isinstance(value, str) or not 32 <= len(value) <= 256
                for value in state.values()
            )
        ):
            raise SystemExit("Invalid saved local collector identity; not replacing credentials")
    else:
        if collector_path.exists() or native.get("COLLECTOR_SERVICE_TOKEN"):
            raise SystemExit("Existing collector credentials require review before adoption")
        state = {
            "service_token": secrets.token_urlsafe(48),
            "db_password": secrets.token_urlsafe(32),
        }
        private_write(STATE, json.dumps(state))
    if native.get("COLLECTOR_SERVICE_TOKEN", state["service_token"]) != state["service_token"]:
        raise SystemExit("Existing API/collector identity does not match the saved local identity")
    if collector.get("COLLECTOR_DB_PASSWORD", state["db_password"]) != state["db_password"]:
        raise SystemExit("Existing collector database identity does not match local state")
    if collector.get("COLLECTOR_SERVICE_TOKEN", state["service_token"]) != state["service_token"]:
        raise SystemExit("Existing collector service identity does not match local state")
    native.update(
        QTRADE_ENV="local",
        QTRADE_LIVE_TRADING_ENABLED="false",
        QTRADE_COLLECTOR_URL="http://127.0.0.1:18100",
        QTRADE_COLLECTOR_ALLOW_HTTP="true",
        COLLECTOR_SERVICE_TOKEN=state["service_token"],
    )
    collector.update(
        COLLECTOR_SERVICE_TOKEN=state["service_token"],
        COLLECTOR_DB_PASSWORD=state["db_password"],
        COLLECTOR_LOCAL_AUTHORIZER_URL="http://127.0.0.1:3122/internal/data/authorize",
        COLLECTOR_ALLOW_HTTP="true",
        COLLECTOR_BIND_ADDRESS="127.0.0.1",
        MAIRUI_LICENCE=native.get("MAIRUI_LICENCE", collector.get("MAIRUI_LICENCE", "")),
        COLLECTOR_MAIRUI_PRO_ENABLED=native.get(
            "QTRADE_MAIRUI_PRO_ENABLED", collector.get("COLLECTOR_MAIRUI_PRO_ENABLED", "false")
        ),
    )
    save_env(native_path, native)
    save_env(collector_path, collector)
    print(
        json.dumps(
            {
                "local_configuration": "prepared",
                "provider_credential_present": bool(collector["MAIRUI_LICENCE"]),
                "live_trading_enabled": False,
            }
        )
    )


if __name__ == "__main__":
    main()
