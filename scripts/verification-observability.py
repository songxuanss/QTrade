"""Exercise deployed process telemetry without printing URLs or credentials."""

import json
import subprocess
from pathlib import Path

import httpx


def compose(service: str, code: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", service, "python", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{service} telemetry verification failed"
    return result.stdout


for service, port in (("api", 3122), ("worker", 3123)):
    with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        assert client.get("/not-a-real-route").status_code == 404
        metrics = client.get("/metrics").text
        assert "qtrade_database_metrics_up 1.0" in metrics
        assert any(
            'route="unmatched"' in line and 'status="404"' in line
            for line in metrics.splitlines()
        )
        assert "qtrade_broker_configured 0.0" in metrics
        if service == "worker":
            assert 'operation="collection.consume",outcome="success"' in metrics
    print(f"PASS: deployed {service} probes, bounded HTTP and durable-queue metrics")

spans = []
for line in Path(".tools/verification-local/worker.log").read_text().splitlines():
    try:
        entry = json.loads(line)
        span = json.loads(entry["message"])
        if span.get("operation") == "collection.consume":
            spans.append(span)
    except (ValueError, KeyError, TypeError):
        continue
for period in ("1d", "5m"):
    result = json.loads(
        Path(f".tools/verification-local/review/history-{period}-results.json").read_text()
    )
    assert any(span["object_id"] == result["taskId"] for span in spans)
print("PASS: actual historical download jobs correlate with exported OpenTelemetry spans")

compose(
    "realtime",
    "import httpx; c=httpx.Client(base_url='http://127.0.0.1:8000'); "
    "assert c.get('/health').status_code==200; "
    "assert c.get('/ready').status_code==200; "
    "m=c.get('/metrics').text; assert 'qtrade_database_metrics_up 1.0' in m; "
    "assert 'qtrade_dependency_up' in m",
)
print("PASS: deployed realtime readiness and metrics (no live market source claimed)")

compose(
    "worker",
    "import httpx; c=httpx.Client(transport=httpx.HTTPTransport(uds='/run/qtrade/runner.sock'),"
    "base_url='http://runner'); assert c.get('/health').status_code==200; "
    "assert c.get('/ready').status_code==200; "
    "r=c.post('/execute',json={'code':\"print('telemetry verification')\"}); "
    "assert r.status_code==200 and r.json()['status']=='succeeded'; "
    "m=c.get('/metrics').text; "
    "assert 'operation=\"strategy.execute\",outcome=\"success\"' in m; "
    "assert 'qtrade_database_metrics_up 0.0' in m",
)
print("PASS: isolated runner execution through worker socket, readiness, span and metrics")
