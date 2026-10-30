"""Immutable calendar persistence with transactional membership authorization."""

import json
from uuid import UUID

from qtrade_contracts import CalendarSchedule
from qtrade_domain.authorization import Permission
from qtrade_domain.calendar_contracts import from_contract, to_contract
from qtrade_domain.calendars import CalendarSnapshot
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.workspaces import PostgresWorkspaceStore


def snapshot(payload: object) -> CalendarSnapshot:
    try:
        return from_contract(CalendarSchedule.model_validate_json(json.dumps(payload)))
    except (ValueError, TypeError):
        raise PortError(ErrorCode.UNAVAILABLE, "invalid stored calendar schedule") from None


class PostgresCalendarStore(PostgresStore):
    def put(self, actor_id: UUID, value: CalendarSnapshot) -> None:
        contract = to_contract(value)
        with self._connection() as conn:
            conn.execute(
                "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE",
                (value.workspace_id,),
            )
            access = PostgresWorkspaceStore._access(conn, actor_id, value.workspace_id)
            if access is None:
                raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
            access.require(Permission.DATA_MANAGE)
            row = conn.execute(
                """SELECT payload FROM calendar_schedule WHERE workspace_id=%s
                AND calendar_id=%s AND calendar_version=%s""",
                (value.workspace_id, contract.calendar_id, contract.calendar_version),
            ).fetchone()
            if row is not None:
                if snapshot(row[0]) != value:
                    raise PortError(ErrorCode.CONFLICT, "calendar version is immutable")
                return
            conn.execute(
                """INSERT INTO calendar_schedule(workspace_id,calendar_id,calendar_version,
                payload,imported_by) VALUES (%s,%s,%s,%s::jsonb,%s)""",
                (
                    value.workspace_id,
                    contract.calendar_id,
                    contract.calendar_version,
                    contract.model_dump_json(),
                    actor_id,
                ),
            )

    def get(
        self, workspace_id: UUID, calendar_id: str, version: int | None = None
    ) -> CalendarSnapshot:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT payload FROM calendar_schedule WHERE workspace_id=%s
                AND calendar_id=%s AND (%s::integer IS NULL OR calendar_version=%s)
                ORDER BY calendar_version DESC LIMIT 1""",
                (workspace_id, calendar_id, version, version),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.NOT_FOUND, "calendar unavailable")
        return snapshot(row[0])
