"""Tenant and current-membership checks before returning whitelisted audit fields."""

import json
from datetime import datetime
from ipaddress import ip_network
from uuid import UUID

from qtrade_domain.audit_port import AuditDetails
from qtrade_domain.audit_query import AuditQuery
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.identity import Role
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.workspaces import PostgresWorkspaceStore


class PostgresAuditQuery(PostgresStore):
    def query(
        self, access: WorkspaceAccess, query: AuditQuery, limit: int
    ) -> tuple[dict[str, object], ...]:
        if type(limit) is not int or not 1 <= limit <= 5001:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid audit query size")
        with self._connection() as conn:
            actual = PostgresWorkspaceStore._access(
                conn, access.user_id, access.workspace.workspace_id
            )
            if actual is None:
                raise PortError(ErrorCode.NOT_FOUND, "workspace unavailable")
            actual.require(Permission.AUDIT_READ)
            rows = conn.execute(
                """SELECT event_id,workspace_id,actor_type,actor_id,action,result,occurred_at,
                source,source_ip,trace_id,details FROM audit_event
                WHERE workspace_id=%s AND occurred_at>=%s AND occurred_at<%s
                AND (%s::uuid IS NULL OR actor_id=%s::uuid)
                AND (%s::text IS NULL OR action=%s::text)
                AND (%s::text IS NULL OR details->>'target_id'=%s::text)
                AND (%s::text IS NULL OR result=%s::text)
                AND (%s::uuid IS NULL OR trace_id=%s::uuid)
                AND (%s::bigint IS NULL OR event_id<%s::bigint)
                ORDER BY event_id DESC LIMIT %s""",
                (
                    access.workspace.workspace_id,
                    query.start,
                    query.end,
                    query.actor_id,
                    query.actor_id,
                    query.action,
                    query.action,
                    query.target_id,
                    query.target_id,
                    query.result,
                    query.result,
                    query.trace_id,
                    query.trace_id,
                    query.before,
                    query.before,
                    limit,
                ),
            ).fetchall()
        return tuple(_safe_row(row) for row in rows)


def _safe_row(row: tuple[object, ...]) -> dict[str, object]:
    try:
        raw = row[10]
        if not isinstance(raw, dict) or not isinstance(row[6], datetime):
            raise ValueError
        # Do not forward raw JSON, session digests, opaque metadata or credentials.
        values: dict[str, object] = {}
        for key in ("target_id", "target_workspace_id"):
            if raw.get(key) is not None:
                values[key] = UUID(str(raw[key]))
        for key in ("permission", "reason", "role_before", "role_after"):
            if raw.get(key) is not None:
                kind = Permission if key == "permission" else ErrorCode if key == "reason" else Role
                values[key] = kind(str(raw[key]))
        for key in ("active_before", "active_after"):
            if raw.get(key) is not None:
                values[key] = raw[key]
        safe = AuditDetails(**values)  # type: ignore[arg-type]
        ip = str(row[8]) if row[8] is not None else None
        network = (
            str(ip_network(f"{ip}/{'64' if ':' in ip else '24'}", strict=False)) if ip else None
        )
        return {
            "event_id": int(str(row[0])),
            "workspace_id": str(row[1]),
            "actor_type": str(row[2]),
            "actor_id": str(row[3]) if row[3] else None,
            "action": str(row[4]),
            "result": str(row[5]),
            "occurred_at": row[6].isoformat(),
            "source": str(row[7]),
            "source_network": network,
            "trace_id": str(row[9]),
            "details": json.loads(safe.canonical_json()),
        }
    except (TypeError, ValueError):
        raise PortError(ErrorCode.UNAVAILABLE, "invalid stored audit projection") from None
