"""PostgreSQL adapter for workspace universe membership intervals."""

import json
from datetime import datetime
from uuid import UUID

from qtrade_contracts import UniverseMembership
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.query import column_query

_COLUMNS = "workspace_id,membership_id,payload"


class PostgresMembershipStore(PostgresStore):
    """Intervals are immutable; corrections are new intervals after review."""

    @staticmethod
    def _record(row: tuple[object, ...]) -> UniverseMembership:
        try:
            workspace_id = UUID(str(row[0]))
            membership_id = UUID(str(row[1]))
            membership = UniverseMembership.model_validate_json(json.dumps(row[2]))
            if (membership.workspace_id, membership.membership_id) != (
                workspace_id,
                membership_id,
            ):
                raise ValueError
            return membership
        except (TypeError, ValueError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored membership") from None

    def create(self, membership: UniverseMembership) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO universe_membership(workspace_id,membership_id,instrument_id,"
                "effective_from,effective_to,source,evidence,created_by,created_at,"
                "content_version,payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (
                    membership.workspace_id,
                    membership.membership_id,
                    membership.instrument_id,
                    membership.effective_from,
                    membership.effective_to,
                    membership.source,
                    membership.evidence,
                    membership.created_by,
                    membership.created_at,
                    membership.content_version,
                    membership.model_dump_json(),
                ),
            )

    def overlaps(
        self, workspace_id: UUID, instrument_id: str, start: datetime, end: datetime
    ) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM universe_membership WHERE workspace_id=%s AND instrument_id=%s "
                "AND effective_from < %s AND effective_to > %s LIMIT 1",
                (workspace_id, instrument_id, end, start),
            ).fetchone()
        return row is not None

    def list(
        self,
        workspace_id: UUID,
        instrument_id: str | None = None,
        limit: int = 200,
    ) -> tuple[UniverseMembership, ...]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid membership page size")
        query = "SELECT {} FROM universe_membership WHERE workspace_id=%s"
        params: list[object] = [workspace_id]
        if instrument_id is not None:
            query += " AND instrument_id=%s"
            params.append(instrument_id)
        query += " ORDER BY effective_from DESC,membership_id LIMIT %s"
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(column_query(query, _COLUMNS), tuple(params)).fetchall()
        return tuple(self._record(row) for row in rows)

    def for_instruments(
        self, workspace_id: UUID, instrument_ids: tuple[str, ...]
    ) -> tuple[UniverseMembership, ...]:
        if not instrument_ids:
            return ()
        with self._connection() as conn:
            rows = conn.execute(
                column_query(
                    "SELECT {} FROM universe_membership WHERE workspace_id=%s "
                    "AND instrument_id = ANY(%s) ORDER BY instrument_id,effective_from",
                    _COLUMNS,
                ),
                (workspace_id, list(instrument_ids)),
            ).fetchall()
        return tuple(self._record(row) for row in rows)
