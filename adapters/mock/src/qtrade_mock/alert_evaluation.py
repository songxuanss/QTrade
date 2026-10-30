"""Serialized in-memory cooldown/escalation state for evaluator tests."""

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from uuid import UUID

from qtrade_domain.alerts import AlertSeverity
from qtrade_domain.ports import ErrorCode, PortError


@dataclass
class _State:
    active_since: datetime | None = None
    last_emitted_at: datetime | None = None
    last_emitted_severity: AlertSeverity | None = None


class MockAlertEvaluationStore:
    def __init__(self) -> None:
        self._states: dict[tuple[UUID, UUID], _State] = {}
        self._lock = RLock()

    def decision(
        self,
        workspace_id: UUID,
        rule_id: UUID,
        triggered: bool,
        now: datetime,
        cooldown_seconds: int,
        severity: AlertSeverity,
        escalation_after_seconds: int,
        silenced: bool,
    ) -> AlertSeverity | None:
        if (
            now.tzinfo is None
            or type(cooldown_seconds) is not int
            or cooldown_seconds < 0
            or type(escalation_after_seconds) is not int
            or escalation_after_seconds < 1
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert evaluation state")
        with self._lock:
            state = self._states.setdefault((workspace_id, rule_id), _State())
            if not triggered:
                state.active_since = None
                state.last_emitted_at = None
                state.last_emitted_severity = None
                return None
            if state.active_since is None:
                state.active_since = now
            effective = severity
            if (now - state.active_since).total_seconds() >= escalation_after_seconds:
                effective = {
                    AlertSeverity.INFO: AlertSeverity.WARNING,
                    AlertSeverity.WARNING: AlertSeverity.CRITICAL,
                    AlertSeverity.CRITICAL: AlertSeverity.CRITICAL,
                }[severity]
            if silenced and effective != AlertSeverity.CRITICAL:
                return None
            escalated = (
                state.last_emitted_severity is not None
                and _SEVERITY_RANK[effective] > _SEVERITY_RANK[state.last_emitted_severity]
            )
            if (
                state.last_emitted_at is not None
                and (now - state.last_emitted_at).total_seconds() < cooldown_seconds
                and not escalated
            ):
                return None
            state.last_emitted_at = now
            state.last_emitted_severity = effective
            return effective


_SEVERITY_RANK = {
    AlertSeverity.INFO: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.CRITICAL: 2,
}
