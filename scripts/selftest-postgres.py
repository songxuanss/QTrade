"""Run real migration/model integration tests on isolated local Compose schemas."""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    container = subprocess.check_output(
        ["docker", "compose", "ps", "-q", "postgres"], cwd=ROOT, text=True
    ).strip()
    if not container:
        raise RuntimeError("start the local Compose PostgreSQL first")
    state = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
    environment = dict(item.split("=", 1) for item in state["Config"]["Env"])
    address = next(iter(state["NetworkSettings"]["Networks"].values()))["IPAddress"]
    user = quote(environment["POSTGRES_USER"], safe="")
    password = quote(environment["POSTGRES_PASSWORD"], safe="")
    database = quote(environment["POSTGRES_DB"], safe="")
    test_environment = {
        **os.environ,
        "QTRADE_TEST_POSTGRES_URL": f"postgresql://{user}:{password}@{address}:5432/{database}",
    }
    objects = subprocess.check_output(
        ["docker", "compose", "ps", "-q", "object-store"], cwd=ROOT, text=True
    ).strip()
    if objects:
        object_state = json.loads(
            subprocess.check_output(["docker", "inspect", objects], text=True)
        )[0]
        object_environment = dict(item.split("=", 1) for item in object_state["Config"]["Env"])
        object_address = next(iter(object_state["NetworkSettings"]["Networks"].values()))[
            "IPAddress"
        ]
        test_environment.update(
            {
                "QTRADE_TEST_OBJECTSTORE_ENDPOINT": f"http://{object_address}:9000",
                "QTRADE_TEST_OBJECTSTORE_ACCESS": object_environment["MINIO_ROOT_USER"],
                "QTRADE_TEST_OBJECTSTORE_SECRET": object_environment["MINIO_ROOT_PASSWORD"],
            }
        )
    return subprocess.call(
        [sys.executable, "-m", "pytest", "tests/test_postgres_integration.py", "-v", *sys.argv[1:]],
        cwd=ROOT,
        env=test_environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
