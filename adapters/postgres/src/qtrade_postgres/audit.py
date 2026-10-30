"""Append-only facts; shared insert supports atomic business transactions."""

from datetime import datetime
from uuid import UUID, uuid4

import psycopg

from qtrade_domain.audit_port import AuditContext, AuditDetails, AuditRecord, contextualize
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore


def append_in_transaction(conn: psycopg.Connection[tuple[object, ...]], record: AuditRecord) -> int:
    row = conn.execute(
        """INSERT INTO audit_event(workspace_id,actor_type,actor_id,action,result,
        occurred_at,source,source_ip,trace_id,details) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        RETURNING event_id""",
        (
            record.workspace_id,
            record.actor_type,
            record.actor_id,
            record.action,
            record.result,
            record.occurred_at,
            record.source,
            record.source_ip,
            record.trace_id,
            record.details.canonical_json(),
        ),
    ).fetchone()
    if row is None:
        raise PortError(ErrorCode.UNAVAILABLE, "audit persistence unavailable")
    return int(str(row[0]))


class PostgresAuditStore(PostgresStore):
    def append(self, record: AuditRecord) -> int:
        with self._connection() as conn:
            return append_in_transaction(conn, record)


def append_logout_rows(
    conn: psycopg.Connection[tuple[object, ...]],
    rows: list[tuple[object, ...]],
    now: datetime,
    source: str,
    *,
    audit_context: AuditContext | None = None,
) -> None:
    trace = uuid4()
    scopes = {
        (UUID(str(row[0])), UUID(str(row[1])) if row[1] is not None else None) for row in rows
    }
    for user, workspace in scopes:
        append_in_transaction(
            conn,
            contextualize(
                AuditRecord(
                    workspace,
                    "user",
                    user,
                    "auth.logout",
                    "success",
                    now,
                    source,
                    trace,
                    details=AuditDetails(target_id=user),
                ),
                audit_context,
            ),
        )
