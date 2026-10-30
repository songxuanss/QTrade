"""Serialize switch changes and Paper settlement, including absent switch rows."""

from uuid import UUID

import psycopg

from qtrade_domain.ports import ErrorCode, PortError


def lock_workspace_trading(
    conn: psycopg.Connection[tuple[object, ...]], workspace_id: UUID
) -> None:
    row = conn.execute(
        "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE", (workspace_id,)
    ).fetchone()
    if row is None:
        raise PortError(ErrorCode.NOT_FOUND, "trading workspace unavailable")
