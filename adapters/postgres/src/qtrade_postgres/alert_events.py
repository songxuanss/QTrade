"""Durable, workspace-scoped alert event adapter with source idempotency."""

import json
from datetime import datetime
from typing import cast
from uuid import UUID

from qtrade_domain.alerts import (
    AlertAction,
    AlertDisposition,
    AlertEvent,
    AlertSeverity,
    AlertSource,
    AlertStatus,
)
from qtrade_domain.authorization import Permission
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.query import column_query


class PostgresAlertEventStore(PostgresStore):
    _COLUMNS = (
        "event_id,workspace_id,source,source_event_id,event_type,severity,status,"
        "title,message,resource_id,occurred_at,updated_at,context_snapshot,assignee_id,silenced_until"
    )

    @staticmethod
    def _decode(row: tuple[object, ...]) -> AlertEvent:
        try:
            return AlertEvent(
                UUID(str(row[0])),
                UUID(str(row[1])),
                AlertSource(str(row[2])),
                str(row[3]),
                str(row[4]),
                AlertSeverity(str(row[5])),
                AlertStatus(str(row[6])),
                str(row[7]),
                str(row[8]),
                str(row[9]) if row[9] is not None else None,
                cast(datetime, row[10]),
                cast(datetime, row[11]),
                json.dumps(row[12], separators=(",", ":"), sort_keys=True),
                UUID(str(row[13])) if row[13] is not None else None,
                cast(datetime, row[14]) if row[14] is not None else None,
            )
        except (TypeError, ValueError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored alert event") from None

    def record(self, event: AlertEvent) -> AlertEvent:
        values = (
            event.event_id,
            event.workspace_id,
            event.source.value,
            event.source_event_id,
            event.event_type,
            event.severity.value,
            event.status.value,
            event.title,
            event.message,
            event.resource_id,
            event.occurred_at,
            event.updated_at,
            event.context_snapshot_json,
            event.assignee_id,
            event.silenced_until,
        )
        with self._connection() as conn:
            conn.execute(
                column_query(
                    "INSERT INTO alert_event ({}) VALUES (%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT "
                    "(workspace_id,source,source_event_id) DO NOTHING",
                    self._COLUMNS,
                ),
                values,
            )
            row = conn.execute(
                column_query(
                    "SELECT {} FROM alert_event WHERE workspace_id=%s AND"
                    " source=%s AND source_event_id=%s",
                    self._COLUMNS,
                ),
                (event.workspace_id, event.source.value, event.source_event_id),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.UNAVAILABLE, "alert event persistence unavailable")
        existing = self._decode(row)
        if (
            existing.event_type,
            existing.severity,
            existing.title,
            existing.message,
            existing.resource_id,
            existing.occurred_at,
            existing.context_snapshot_json,
        ) != (
            event.event_type,
            event.severity,
            event.title,
            event.message,
            event.resource_id,
            event.occurred_at,
            event.context_snapshot_json,
        ):
            raise PortError(ErrorCode.CONFLICT, "alert source event identity reused")
        return existing

    def list(self, workspace_id: UUID, limit: int) -> tuple[AlertEvent, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert event limit")
        with self._connection() as conn:
            rows = conn.execute(
                column_query(
                    "SELECT {} FROM alert_event WHERE workspace_id=%s "
                    "ORDER BY occurred_at DESC,event_id LIMIT %s",
                    self._COLUMNS,
                ),
                (workspace_id, limit),
            ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def get(self, workspace_id: UUID, event_id: UUID) -> AlertEvent | None:
        with self._connection() as conn:
            row = conn.execute(
                column_query(
                    "SELECT {} FROM alert_event WHERE workspace_id=%s AND event_id=%s",
                    self._COLUMNS,
                ),
                (workspace_id, event_id),
            ).fetchone()
        return self._decode(row) if row else None

    def history(self, workspace_id: UUID, event_id: UUID) -> tuple[AlertDisposition, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT event_id,workspace_id,actor_id,action,note,silenced_until,occurred_at "
                "FROM alert_disposition_history WHERE workspace_id=%s AND event_id=%s "
                "ORDER BY occurred_at,history_id",
                (workspace_id, event_id),
            ).fetchall()
        try:
            return tuple(
                AlertDisposition(
                    UUID(str(r[0])),
                    UUID(str(r[1])),
                    UUID(str(r[2])),
                    cast(AlertAction, str(r[3])),
                    cast(str | None, r[4]),
                    cast(datetime | None, r[5]),
                    cast(datetime, r[6]),
                )
                for r in rows
            )
        except (TypeError, ValueError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid alert history") from None

    def handle(
        self,
        actor_id: UUID,
        workspace_id: UUID,
        event_id: UUID,
        action: AlertAction,
        occurred_at: datetime,
        note: str | None = None,
        silenced_until: datetime | None = None,
    ) -> AlertEvent:
        from qtrade_postgres.workspaces import PostgresWorkspaceStore

        with self._connection() as conn:
            access = PostgresWorkspaceStore._access(conn, actor_id, workspace_id)
            if access is None:
                raise PortError(ErrorCode.NOT_FOUND, "alert event unavailable")
            access.require(Permission.ALERT_HANDLE)
            row = conn.execute(
                column_query(
                    "SELECT {} FROM alert_event WHERE workspace_id=%s AND event_id=%s FOR UPDATE",
                    self._COLUMNS,
                ),
                (workspace_id, event_id),
            ).fetchone()
            if row is None:
                raise PortError(ErrorCode.NOT_FOUND, "alert event unavailable")
            event = self._decode(row)
            if event.status == AlertStatus.CLOSED and action != "note":
                raise PortError(ErrorCode.CONFLICT, "closed alert cannot be changed")
            assignee, status, silence = event.assignee_id, event.status, event.silenced_until
            if action == "claim":
                if assignee not in (None, actor_id):
                    raise PortError(ErrorCode.CONFLICT, "alert already claimed")
                assignee = actor_id
            elif action == "acknowledge":
                status = AlertStatus.ACKNOWLEDGED
            elif action == "silence":
                silence = silenced_until
            elif action == "close":
                status = AlertStatus.CLOSED
            changed = (assignee, status, silence) != (
                event.assignee_id,
                event.status,
                event.silenced_until,
            )
            if changed or action == "note":
                conn.execute(
                    "UPDATE alert_event SET assignee_id=%s,status=%s,silenced_until=%s,"
                    "updated_at=%s "
                    "WHERE workspace_id=%s AND event_id=%s",
                    (assignee, status.value, silence, occurred_at, workspace_id, event_id),
                )
                conn.execute(
                    "INSERT INTO alert_disposition_history "
                    "(workspace_id,event_id,actor_id,action,note,silenced_until,occurred_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (workspace_id, event_id, actor_id, action, note, silenced_until, occurred_at),
                )
            return AlertEvent(
                event.event_id,
                event.workspace_id,
                event.source,
                event.source_event_id,
                event.event_type,
                event.severity,
                status,
                event.title,
                event.message,
                event.resource_id,
                event.occurred_at,
                occurred_at if changed or action == "note" else event.updated_at,
                event.context_snapshot_json,
                assignee,
                silence,
            )
