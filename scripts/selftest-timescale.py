"""Run real TimescaleDB storage tests in an isolated temporary container."""

import json
import os
import secrets
import subprocess
import sys
import time
from uuid import uuid4

import psycopg

IMAGE = (
    "timescale/timescaledb:2.30.2-pg17@sha256:"
    "b346edcdb51a1fd6020e3965e0bd1c9f3406fa6d5fbce1e28f4852587ef934e2"
)


def main() -> int:
    name = "qtrade-timescale-test-" + uuid4().hex[:12]
    password = secrets.token_urlsafe(24)
    created = False
    try:
        subprocess.check_output(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-e",
                "POSTGRES_PASSWORD=" + password,
                IMAGE,
            ]
        )
        created = True
        state = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
        address = next(iter(state["NetworkSettings"]["Networks"].values()))["IPAddress"]
        url = f"postgresql://postgres:{password}@{address}:5432/postgres"
        for _ in range(60):
            try:
                with psycopg.connect(url, connect_timeout=1):
                    break
            except psycopg.Error:
                time.sleep(1)
        else:
            raise RuntimeError("test TimescaleDB did not become ready")
        return subprocess.call(
            [sys.executable, "-m", "pytest", "tests/test_timescale_integration.py", "-v"],
            env={**os.environ, "QTRADE_TEST_TIMESCALE_URL": url},
        )
    finally:
        if created:
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
