"""Durable work items submitted to the alert evaluator."""

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from qtrade_domain.alerts import AlertSource
from qtrade_domain.ports import ErrorCode, PortError

FactValue = str | int | float | bool


def _aware(value: datetime) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


@dataclass(frozen=True, slots=True)
class AlertEvaluationJob:
    job_id: UUID
    workspace_id: UUID
    actor_id: UUID
    source: AlertSource
    facts: tuple[tuple[str, FactValue], ...]
    occurred_at: datetime
    resource_id: str | None = None
    context_snapshot_json: str = "{}"

    def __post_init__(self) -> None:
        canonical_context = _canonical_json_object(self.context_snapshot_json)
        if not _aware(self.occurred_at):
            raise PortError(ErrorCode.INVALID_INPUT, "alert fact timestamp must be aware")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        if (
            not isinstance(self.job_id, UUID)
            or not isinstance(self.workspace_id, UUID)
            or not isinstance(self.actor_id, UUID)
            or not isinstance(self.source, AlertSource)
            or not isinstance(self.facts, tuple)
            or not 1 <= len(self.facts) <= 64
            or any(not isinstance(item, tuple) or len(item) != 2 for item in self.facts)
            or any(
                not isinstance(key, str)
                or not 1 <= len(key) <= 64
                or key.startswith("_")
                or type(value) not in {str, int, float, bool}
                or (type(value) is float and not math.isfinite(value))
                or (type(value) is str and len(value) > 512)
                for key, value in self.facts
            )
            or len({key for key, _ in self.facts}) != len(self.facts)
            or (
                self.resource_id is not None
                and (not isinstance(self.resource_id, str) or len(self.resource_id) > 256)
            )
            or not isinstance(self.context_snapshot_json, str)
            or len(self.context_snapshot_json) > 65536
            or canonical_context is None
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert evaluation job")
        object.__setattr__(self, "context_snapshot_json", canonical_context)


def _canonical_json_object(value: str) -> str | None:
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            return None
        return json.dumps(parsed, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class AlertEvaluationLease:
    job: AlertEvaluationJob
    token: UUID
    attempts: int


class AlertEvaluationQueuePort(Protocol):
    """Durable queue with expiring, fenced worker leases."""

    def enqueue(self, job: AlertEvaluationJob) -> AlertEvaluationJob: ...

    def ready(
        self, now: datetime, limit: int, lease_seconds: int
    ) -> tuple[AlertEvaluationLease, ...]: ...

    def finish(
        self,
        lease: AlertEvaluationLease,
        now: datetime,
        *,
        succeeded: bool,
        max_attempts: int = 8,
    ) -> bool: ...
