"""First start of the existing native SSO/API/Worker/Web verification profile.

Rebinds only private connection hosts after a WSL restart, retaining generated
identities and databases. Start windows-qtrade.ps1 -Action keepalive first.
"""

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if os.environ.get("WSL_DISTRO_NAME") != "QTrade-Ubuntu-26":
        raise SystemExit("Use the dedicated Ubuntu 26 environment")
    if (ROOT / ".tools/verification-local/processes.json").exists():
        raise SystemExit("Native processes are already registered; inspect status/recovery first")
    sys.path[:0] = [
        str(path / "src")
        for group in ("apps", "packages", "adapters")
        for path in (ROOT / group).iterdir()
        if (path / "src").is_dir()
    ]
    helper = runpy.run_path(str(ROOT / "scripts/verification-local.py"))
    dependencies = {
        service: helper["container_environment"](service)
        for service in ("postgres", "market-db", "valkey", "object-store")
    }
    bypass = ",".join(
        ["localhost", "127.0.0.1", "::1", *[value[0] for value in dependencies.values()]]
    )
    os.environ.update(NO_PROXY=bypass, no_proxy=bypass)
    state_path = ROOT / ".tools/sso-local/state.json"
    state = json.loads(state_path.read_text())
    previous = dict(state)
    host = dependencies["postgres"][0]
    for key in ("prefix", "api_url", "sso_url"):
        parts = urlsplit(state[key])
        credentials = parts.netloc.rsplit("@", 1)[0]
        state[key] = urlunsplit(parts._replace(netloc=f"{credentials}@{host}:{parts.port or 5432}"))
    if state != previous:
        writer = runpy.run_path(str(ROOT / "scripts/prepare-wsl-collector.py"))["private_write"]
        writer(state_path, json.dumps(state))
    with psycopg.connect(state["prefix"] + "postgres", connect_timeout=5) as connection:
        found = connection.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)", (state["databases"],)
        ).fetchall()
    if {row[0] for row in found} != set(state["databases"]):
        raise SystemExit("Existing isolated databases are missing; refusing to reseed identities")
    from qtrade_object_store.s3 import S3ObjectStore

    address, values = dependencies["object-store"]
    objects = S3ObjectStore.connect(
        bucket="qtrade-verification",
        endpoint_url=f"http://{address}:9000",
        allow_http=True,
        access_key=values["MINIO_ROOT_USER"],
        secret_key=values["MINIO_ROOT_PASSWORD"],
    )
    objects.ensure_bucket()
    print("Existing isolated databases retained; local object bucket ready", flush=True)
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), "scripts/verification-local.py", "start"],
        cwd=ROOT,
        env=os.environ.copy(),
        check=True,
    )


if __name__ == "__main__":
    main()
