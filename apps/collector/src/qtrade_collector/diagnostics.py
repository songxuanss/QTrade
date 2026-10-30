"""Allowlisted JSON logging: no URL, auth headers, exception text or upstream payloads."""

import json
import logging
from datetime import UTC, datetime

from qtrade_data_core.diagnostics import collection_context

FIELDS = (
    "scope_id",
    "schedule_id",
    "event",
    "correlation_id",
    "job_id",
    "part_id",
    "source",
    "stage",
    "attempt",
    "duration_ms",
    "error_code",
    "state",
    "http_status",
    "provider_code",
    "records",
    "response_bytes",
    "pace_wait_ms",
    "transport_ms",
    "usage_request_id",
)


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        output: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": "data-collector",
        }
        for field in FIELDS:
            value = getattr(record, field, (collection_context.get() or {}).get(field))
            if value is not None and isinstance(value, str | int | float):
                output[field] = value
        # Unknown library messages and exception traces are deliberately excluded.
        output.setdefault("event", "runtime")
        return json.dumps(output, separators=(",", ":"))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for name in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)
