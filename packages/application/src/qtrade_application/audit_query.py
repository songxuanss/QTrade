"""Scoped audit search and bounded, injection-safe CSV export."""

import csv
import io
import json
from datetime import UTC, datetime
from uuid import uuid4

from qtrade_domain.audit_port import (
    AuditContext,
    AuditDetails,
    AuditRecord,
    AuditStorePort,
    contextualize,
)
from qtrade_domain.audit_query import AuditQuery, AuditQueryPort
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError

COLUMNS = (
    "event_id",
    "workspace_id",
    "occurred_at",
    "actor_type",
    "actor_id",
    "action",
    "result",
    "trace_id",
    "source",
    "source_network",
    "details",
)


class AuditQueryService:
    def __init__(self, queries: AuditQueryPort, audit: AuditStorePort):
        self._queries, self._audit = queries, audit

    def search(
        self, access: WorkspaceAccess, query: AuditQuery, limit: int = 100
    ) -> dict[str, object]:
        access.require(Permission.AUDIT_READ)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid audit page size")
        rows = self._rows(access, query, limit + 1)
        return {
            "events": list(rows[:limit]),
            "next_before": rows[limit - 1]["event_id"] if len(rows) > limit else None,
        }

    def export(self, access: WorkspaceAccess, query: AuditQuery, context: AuditContext) -> bytes:
        access.require(Permission.AUDIT_EXPORT)
        # An export always begins at the selected range, independent of table pagination.
        if query.before is not None:
            raise PortError(ErrorCode.INVALID_INPUT, "export cannot use a page cursor")
        rows = self._rows(access, query, 5001)
        if len(rows) > 5000:
            raise PortError(ErrorCode.INVALID_INPUT, "narrow audit export range")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([self._cell(row.get(column)) for column in COLUMNS])
        self._audit.append(
            contextualize(
                AuditRecord(
                    access.workspace.workspace_id,
                    "user",
                    access.user_id,
                    "audit.export",
                    "success",
                    datetime.now(UTC),
                    "audit.export",
                    uuid4(),
                    details=AuditDetails(target_workspace_id=access.workspace.workspace_id),
                ),
                context,
            )
        )
        return ("\ufeff" + output.getvalue()).encode("utf-8")

    def _rows(
        self, access: WorkspaceAccess, query: AuditQuery, limit: int
    ) -> tuple[dict[str, object], ...]:
        rows = self._queries.query(access, query, limit)
        if len(rows) > limit or any(
            row.get("workspace_id") != str(access.workspace.workspace_id)
            or type(row.get("event_id")) is not int
            for row in rows
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid audit result scope")
        return rows

    @staticmethod
    def _cell(value: object) -> str:
        text = (
            json.dumps(value, sort_keys=True, separators=(",", ":"))
            if isinstance(value, dict)
            else str(value)
            if value is not None
            else ""
        )
        return (
            "'" + text
            if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n"))
            else text
        )
