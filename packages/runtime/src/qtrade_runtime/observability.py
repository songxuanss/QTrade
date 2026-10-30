"""OpenTelemetry spans, bounded Prometheus labels and secret-safe JSON logs."""

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from psycopg import sql


class JsonLogFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service
        self.secrets = set()
        for key, value in os.environ.items():
            if (
                any(part in key for part in ("SECRET", "PASSWORD", "TOKEN", "LICENCE", "KEY"))
                and len(value) >= 6
            ):
                self.secrets.add(value)
                try:
                    values = json.loads(value)
                    if isinstance(values, dict):
                        self.secrets.update(
                            item
                            for item in values.values()
                            if isinstance(item, str) and len(item) >= 6
                        )
                except ValueError:
                    pass
            if key.endswith("URL"):
                try:
                    password = urlsplit(value).password
                    if password:
                        self.secrets.add(password)
                except ValueError:
                    pass

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        for secret in sorted(self.secrets, key=len, reverse=True):
            message = message.replace(secret, "[redacted]")
        message = re.sub(r"(?:https?|postgres(?:ql)?|redis)://\S+", "[url]", message)
        body: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "service": self.service,
            "level": record.levelname,
            "logger": record.name,
            "message": message[:2048],
        }
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            body["trace_id"] = str(UUID(int=context.trace_id))
            body["span_id"] = f"{context.span_id:016x}"
        if record.exc_info and record.exc_info[0]:
            # Exception messages, stack frames and locals can contain credentials.
            body["error_type"] = record.exc_info[0].__name__
        return json.dumps(body, separators=(",", ":"))


def configure_logging(service: str) -> None:
    formatter = JsonLogFormatter(service)
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    root.setLevel(logging.INFO)
    for logger in (
        root,
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    ):
        for handler in logger.handlers:
            handler.setFormatter(formatter)


class SafeSpanExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            context = span.get_span_context()
            if context is None:
                continue
            # Deliberately omit attributes/events: no body, SQL, header, URL or code output.
            object_id = (span.attributes or {}).get("qtrade.object_id")
            try:
                safe_id = str(UUID(str(object_id))) if object_id else None
            except ValueError:
                safe_id = None
            logging.getLogger("qtrade.trace").info(
                json.dumps(
                    {
                        "event": "span.completed",
                        "object_id": safe_id,
                        "operation": span.name,
                        "trace_id": str(UUID(int=context.trace_id)),
                        "span_id": f"{context.span_id:016x}",
                        "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
                        "status": span.status.status_code.name,
                        "elapsed_ms": ((span.end_time or 0) - (span.start_time or 0)) / 1_000_000,
                    },
                    separators=(",", ":"),
                )
            )
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


class ServiceTelemetry:
    def __init__(self, service: str):
        self.provider = TracerProvider(resource=Resource.create({"service.name": service}))
        self.provider.add_span_processor(SimpleSpanProcessor(SafeSpanExporter()))
        self.tracer = self.provider.get_tracer("qtrade")
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "qtrade_http_requests_total",
            "HTTP responses by matched route",
            ["service", "method", "route", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "qtrade_http_duration_seconds",
            "HTTP processing time",
            ["service", "method", "route"],
            registry=self.registry,
        )
        self.operations = Counter(
            "qtrade_operations_total",
            "Background operations",
            ["service", "operation", "outcome"],
            registry=self.registry,
        )
        self.operation_duration = Histogram(
            "qtrade_operation_duration_seconds",
            "Background processing time",
            ["service", "operation"],
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "qtrade_queue_depth",
            "Pending durable work; NaN when unavailable",
            ["queue"],
            registry=self.registry,
        )
        self.database_metrics_up = Gauge(
            "qtrade_database_metrics_up",
            "Queue/stream database metrics available",
            registry=self.registry,
        )
        self.market_lag = Gauge(
            "qtrade_market_lag_seconds",
            "Largest persisted market lag; NaN when unobserved",
            registry=self.registry,
        )
        self.market_connections = Gauge(
            "qtrade_market_connections",
            "Persisted stream connection states",
            ["state"],
            registry=self.registry,
        )
        self.broker_configured = Gauge(
            "qtrade_broker_configured", "Real broker adapter configured", registry=self.registry
        )
        self.broker_configured.set(0)
        self.dependencies = Gauge(
            "qtrade_dependency_up",
            "Last readiness dependency probe",
            ["dependency"],
            registry=self.registry,
        )
        self.service = service

    def collect_database_metrics(self) -> None:
        self.database_metrics_up.set(0)
        for queue in ("collection", "backtest", "alerts", "notifications"):
            self.queue_depth.labels(queue).set(float("nan"))
        self.market_lag.set(float("nan"))
        for state in ("healthy", "degraded", "disconnected", "reconnecting"):
            self.market_connections.labels(state).set(float("nan"))
        if not os.environ.get("DATABASE_URL"):
            return
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
                conn.execute("SET statement_timeout='1000ms'")
                for queue, table, states in (
                    ("collection", "ingestion_task", ("queued", "running", "waiting")),
                    ("backtest", "backtest_run", ("queued", "running")),
                    ("alerts", "alert_evaluation_job", ("queued", "running")),
                    ("notifications", "notification_delivery", ("queued", "running")),
                ):
                    # These are fixed infrastructure-owned identifiers, never user inputs.
                    row = conn.execute(
                        sql.SQL("SELECT count(*) FROM qtrade.{} WHERE status=ANY(%s)").format(
                            sql.Identifier(table)
                        ),
                        (list(states),),
                    ).fetchone()
                    if row is not None:
                        self.queue_depth.labels(queue).set(float(row[0]))
                rows = conn.execute(
                    "SELECT status,count(*),max(latest_lag_ms) "
                    "FROM qtrade.market_stream_health GROUP BY status"
                ).fetchall()
                for state in ("healthy", "degraded", "disconnected", "reconnecting"):
                    self.market_connections.labels(state).set(
                        sum(float(row[1]) for row in rows if row[0] == state)
                    )
                lags = [float(row[2]) / 1000 for row in rows if row[2] is not None]
                if lags:
                    self.market_lag.set(max(lags))
            self.database_metrics_up.set(1)
        except psycopg.Error:
            # Absence/failure is not a healthy empty queue or a live market connection.
            for queue in ("collection", "backtest", "alerts", "notifications"):
                self.queue_depth.labels(queue).set(float("nan"))

    @contextmanager
    def operation(self, name: str, object_id: UUID | None = None) -> Iterator[None]:
        started = perf_counter()
        outcome = "success"
        with self.tracer.start_as_current_span(
            name, record_exception=False, set_status_on_exception=False
        ) as span:
            if object_id:
                span.set_attribute("qtrade.object_id", str(object_id))
            try:
                yield
            except BaseException:
                outcome = "failure"
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                self.operations.labels(self.service, name, outcome).inc()
                self.operation_duration.labels(self.service, name).observe(perf_counter() - started)


def instrument_app(
    app: FastAPI, service: str, *, accept_trace_parent: bool = False
) -> ServiceTelemetry:
    configure_logging(service)
    telemetry = ServiceTelemetry(service)
    app.state.telemetry = telemetry

    @app.middleware("http")
    async def instrument(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = perf_counter()
        status = 500
        # Route patterns are known after routing. Never put user paths/queries in labels.
        method = (
            request.method
            if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
            else "OTHER"
        )
        with telemetry.tracer.start_as_current_span(
            "http.request",
            context=(
                TraceContextTextMapPropagator().extract(
                    {"traceparent": request.headers.get("traceparent", "")}
                )
                if accept_trace_parent
                else None
            ),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            request.state.audit_trace = UUID(int=span.get_span_context().trace_id)
            try:
                response = await call_next(request)
                status = response.status_code
                response.headers["X-QTrade-Trace-ID"] = str(request.state.audit_trace)
                return response
            finally:
                route = getattr(request.scope.get("route"), "path", "unmatched")
                span.update_name(f"{method} {route}")
                span.set_attribute("http.response.status_code", status)
                if status >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                telemetry.requests.labels(service, method, route, str(status)).inc()
                telemetry.duration.labels(service, method, route).observe(perf_counter() - started)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        telemetry.collect_database_metrics()
        return Response(
            generate_latest(telemetry.registry),
            headers={"Content-Type": CONTENT_TYPE_LATEST, "Cache-Control": "no-store"},
        )

    return telemetry
