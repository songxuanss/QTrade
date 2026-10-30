"""Transactional runtime state for alert cooldowns across worker restarts."""

from datetime import datetime
from typing import cast
from uuid import UUID

from qtrade_domain.alerts import AlertSeverity
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore


class PostgresAlertEvaluationStore(PostgresStore):
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
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO alert_evaluation_state(workspace_id,rule_id,updated_at) "
                "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (workspace_id, rule_id, now),
            )
            row = conn.execute(
                "SELECT active_since,last_emitted_at,last_emitted_severity "
                "FROM alert_evaluation_state "
                "WHERE workspace_id=%s AND rule_id=%s FOR UPDATE",
                (workspace_id, rule_id),
            ).fetchone()
            if row is None:
                raise PortError(ErrorCode.UNAVAILABLE, "alert evaluation state unavailable")
            active_since, last_emitted_at = (
                cast(datetime | None, row[0]),
                cast(datetime | None, row[1]),
            )
            last_emitted_severity = AlertSeverity(cast(str, row[2])) if row[2] is not None else None
            if not triggered:
                conn.execute(
                    "UPDATE alert_evaluation_state "
                    "SET active_since=NULL,last_emitted_at=NULL,last_emitted_severity=NULL,"
                    "updated_at=%s "
                    "WHERE workspace_id=%s AND rule_id=%s",
                    (now, workspace_id, rule_id),
                )
                return None
            if active_since is None:
                active_since = now
            effective = severity
            if (now - active_since).total_seconds() >= escalation_after_seconds:
                effective = {
                    AlertSeverity.INFO: AlertSeverity.WARNING,
                    AlertSeverity.WARNING: AlertSeverity.CRITICAL,
                    AlertSeverity.CRITICAL: AlertSeverity.CRITICAL,
                }[severity]
            escalated = (
                last_emitted_severity is not None
                and _SEVERITY_RANK[effective] > _SEVERITY_RANK[last_emitted_severity]
            )
            emit = not (silenced and effective != AlertSeverity.CRITICAL) and not (
                last_emitted_at is not None
                and (now - last_emitted_at).total_seconds() < cooldown_seconds
                and not escalated
            )
            conn.execute(
                "UPDATE alert_evaluation_state "
                "SET active_since=%s,last_emitted_at=%s,last_emitted_severity=%s,updated_at=%s "
                "WHERE workspace_id=%s AND rule_id=%s",
                (
                    active_since,
                    now if emit else last_emitted_at,
                    effective.value
                    if emit
                    else (last_emitted_severity.value if last_emitted_severity else None),
                    now,
                    workspace_id,
                    rule_id,
                ),
            )
        return effective if emit else None


_SEVERITY_RANK = {
    AlertSeverity.INFO: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.CRITICAL: 2,
}
