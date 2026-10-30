"""Authorized alert-fact submission to the durable evaluator queue."""

from datetime import UTC, datetime
from uuid import uuid4

from qtrade_application.alert_evaluation import context_snapshot_json
from qtrade_domain.alert_jobs import AlertEvaluationJob, AlertEvaluationQueuePort
from qtrade_domain.alerts import AlertSource
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError


class AlertEvaluationSubmissionService:
    def __init__(self, queue: AlertEvaluationQueuePort):
        self._queue = queue

    def submit(
        self,
        access: WorkspaceAccess,
        source: AlertSource,
        facts: dict[str, str | int | float | bool],
        *,
        occurred_at: datetime | None = None,
        resource_id: str | None = None,
        context_snapshot: dict[str, object] | None = None,
    ) -> AlertEvaluationJob:
        access.require(Permission.ALERT_RULE_WRITE)
        if not isinstance(source, AlertSource) or not isinstance(facts, dict):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert evaluation input")
        sensitive = (
            "password",
            "secret",
            "token",
            "credential",
            "authorization",
            "license",
            "licence",
            "api_key",
        )
        if any(
            any(marker in key.casefold() for marker in sensitive)
            for key in facts
            if isinstance(key, str)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "sensitive values cannot be alert facts")
        pairs = tuple(sorted(facts.items()))
        job = AlertEvaluationJob(
            uuid4(),
            access.workspace.workspace_id,
            access.user_id,
            source,
            pairs,
            occurred_at or datetime.now(UTC),
            resource_id,
            context_snapshot_json(context_snapshot or {}),
        )
        return self._queue.enqueue(job)
