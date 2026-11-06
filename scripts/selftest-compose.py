"""Exercise the running QTrade Compose stack and reversible dependency failure."""

import json
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import urlopen


def request(url):
    try:
        with urlopen(url, timeout=15) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def compose(*args):
    return subprocess.check_output(["docker", "compose", *args], text=True)


def wait_ready(expected):
    for _ in range(30):
        status, body = request("http://127.0.0.1:18000/ready")
        if status == expected:
            return body
        time.sleep(1)
    raise AssertionError(f"readiness never returned {expected}")


def wait_services_healthy(expected):
    for _ in range(60):
        rows = [json.loads(line) for line in compose("ps", "--format", "json").splitlines()]
        if {row["Service"] for row in rows} == expected and all(
            row["Health"] == "healthy" for row in rows
        ):
            return rows
        time.sleep(1)
    raise AssertionError(f"Compose services did not become healthy: {rows}")


def main():
    expected = {
        "web",
        "api",
        "worker",
        "realtime",
        "strategy-runner",
        "postgres",
        "valkey",
        "object-store",
        "market-db",
    }
    wait_services_healthy(expected)
    assert request("http://127.0.0.1:13000/health")[1]["service"] == "qtrade-web"
    assert request("http://127.0.0.1:18000/health")[1]["service"] == "qtrade-api"
    assert all(wait_ready(200)["checks"].values())
    for service in ("worker", "realtime"):
        script = (
            "import json,urllib.request; "
            "d=json.load(urllib.request.urlopen('http://localhost:8000/ready')); "
            "assert d['status']=='ready'; import os; assert os.getuid()!=0"
        )
        compose("exec", "-T", service, "python", "-c", script)
    try:
        compose("stop", "valkey")
        failed = wait_ready(503)
        assert failed["checks"]["cache"] is False
        assert failed["checks"]["database"] is True
        assert request("http://127.0.0.1:18000/health")[0] == 200
    finally:
        compose("start", "valkey")
    assert all(wait_ready(200)["checks"].values())
    try:
        compose("stop", "market-db")
        failed = wait_ready(503)
        assert failed["checks"]["market_database"] is False
        assert failed["checks"]["database"] is True
        assert request("http://127.0.0.1:18000/health")[0] == 200
    finally:
        compose("start", "market-db")
    assert all(wait_ready(200)["checks"].values())
    print("PASS: nine services healthy; non-root processes; dependency failure and recovery")


if __name__ == "__main__":
    main()
