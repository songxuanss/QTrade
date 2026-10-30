"""Tracing and metric labels must preserve correlation without carrying secrets."""

import json
import logging
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from qtrade_runtime.observability import JsonLogFormatter, ServiceTelemetry, instrument_app


def test_request_trace_matches_audit_context_and_metric_route_is_bounded(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = FastAPI()
    telemetry = instrument_app(app, "test-observation")
    exported = InMemorySpanExporter()
    telemetry.provider.add_span_processor(SimpleSpanProcessor(exported))

    @app.get("/items/{item_id}")
    def item(item_id: str, request: Request):
        return {"trace_id": str(request.state.audit_trace)}

    with TestClient(app) as client:
        response = client.get("/items/private-resource?token=private-query")
        assert UUID(response.headers["X-QTrade-Trace-ID"])
        assert response.json()["trace_id"] == response.headers["X-QTrade-Trace-ID"]
        metrics = client.get("/metrics").text
        assert 'route="/items/{item_id}"' in metrics
        assert "private-resource" not in metrics and "private-query" not in metrics
        assert 'qtrade_queue_depth{queue="collection"} NaN' in metrics
        assert "qtrade_database_metrics_up 0.0" in metrics
    spans = exported.get_finished_spans()
    span = next(span for span in spans if span.name == "GET /items/{item_id}")
    assert str(UUID(int=span.context.trace_id)) == response.json()["trace_id"]
    assert "private-query" not in str(span.attributes)


def test_operation_failure_metrics_and_span_identity_are_not_exception_text():
    telemetry = ServiceTelemetry("test-jobs")
    exported = InMemorySpanExporter()
    telemetry.provider.add_span_processor(SimpleSpanProcessor(exported))
    job = uuid4()
    with pytest.raises(RuntimeError):
        with telemetry.operation("collection.consume", job):
            raise RuntimeError("private-job-key")
    span = exported.get_finished_spans()[0]
    assert span.attributes["qtrade.object_id"] == str(job)
    assert span.status.status_code.name == "ERROR" and not span.events
    assert (
        telemetry.operations.labels("test-jobs", "collection.consume", "failure")._value.get() == 1
    )


def test_json_logs_redact_configured_secrets_urls_and_exception_details(monkeypatch):
    monkeypatch.setenv("MAIRUI_LICENCE", "private-provider-license")
    formatter = JsonLogFormatter("api")
    record = logging.LogRecord(
        "provider",
        logging.ERROR,
        "test",
        1,
        "https://example.test/private-provider-license?token=unknown and private-provider-license",
        (),
        None,
    )
    try:
        raise RuntimeError("private-provider-license")
    except RuntimeError:
        import sys

        record.exc_info = sys.exc_info()
    body = json.loads(formatter.format(record))
    assert body["error_type"] == "RuntimeError"
    assert "private-provider-license" not in str(body) and "token=unknown" not in str(body)
    assert body["message"] == "[url] and [redacted]"


def test_runner_readiness_requires_its_execution_socket(monkeypatch, tmp_path):
    import socket

    import qtrade_runner.main as runner

    path = tmp_path / "runner.sock"
    monkeypatch.setattr(runner, "SOCKET", path)
    assert runner.ready().status_code == 503
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        assert runner.ready().status_code == 200


@pytest.mark.parametrize("accept_parent", [True, False])
def test_only_trusted_internal_listener_accepts_trace_parent(monkeypatch, accept_parent):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = FastAPI()
    telemetry = instrument_app(app, "test-trace-parent", accept_trace_parent=accept_parent)
    exported = InMemorySpanExporter()
    telemetry.provider.add_span_processor(SimpleSpanProcessor(exported))

    @app.get("/probe")
    def probe():
        return {"ok": True}

    parent = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    with TestClient(app) as client:
        response = client.get(
            "/probe",
            headers={
                "traceparent": parent,
                "tracestate": "private=private-state-sentinel",
                "baggage": "credential=private-baggage-sentinel",
            },
        )
        trace_id = UUID(response.headers["X-QTrade-Trace-ID"]).int
        assert (trace_id == int("a" * 32, 16)) is accept_parent
        invalid = client.get("/probe", headers={"traceparent": "private-invalid-parent-sentinel"})
        assert UUID(invalid.headers["X-QTrade-Trace-ID"]).int != int("a" * 32, 16)
    span = exported.get_finished_spans()[0]
    assert span.context.trace_state.get("private") is None
    assert "sentinel" not in str(span.attributes)
    if accept_parent:
        assert span.parent.span_id == int("b" * 16, 16)
    else:
        assert span.parent is None
