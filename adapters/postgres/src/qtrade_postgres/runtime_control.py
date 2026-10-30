"""Workspace-scoped execution runtime persistence with optimistic transitions."""

from datetime import datetime
from typing import cast
from uuid import UUID

from qtrade_domain.audit_port import AuditRecord
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.runtime_control import (
    ExecutionRuntime,
    RuntimeMode,
    RuntimeState,
)
from qtrade_postgres.audit import append_in_transaction
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.trading_lock import lock_workspace_trading


class PostgresExecutionRuntimeStore(PostgresStore):
    def get(self, workspace_id: UUID, runtime_id: UUID) -> ExecutionRuntime | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT workspace_id,runtime_id,account_id,strategy_version_id,mode,state,"
                "revision,updated_at FROM execution_runtime "
                "WHERE workspace_id=%s AND runtime_id=%s",
                (workspace_id, runtime_id),
            ).fetchone()
        return _decode(row) if row else None

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[ExecutionRuntime, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid runtime list limit")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT workspace_id,runtime_id,account_id,strategy_version_id,mode,state,"
                "revision,updated_at FROM execution_runtime WHERE workspace_id=%s "
                "ORDER BY updated_at DESC,runtime_id LIMIT %s",
                (workspace_id, limit),
            ).fetchall()
        return tuple(_decode(row) for row in rows)

    def transition(
        self,
        runtime: ExecutionRuntime,
        *,
        expected_revision: int,
        state: RuntimeState,
        updated_at: datetime,
        audit: AuditRecord,
    ) -> ExecutionRuntime:
        if (
            audit.workspace_id != runtime.workspace_id
            or audit.details.target_id != runtime.runtime_id
            or audit.details.target_workspace_id != runtime.workspace_id
            or audit.action
            != {"paused": "runtime.pause", "running": "runtime.resume", "stopped": "runtime.stop"}[
                state
            ]
            or audit.result != "success"
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "runtime audit does not match transition")
        with self._connection() as conn:
            lock_workspace_trading(conn, runtime.workspace_id)
            row = conn.execute(
                "UPDATE execution_runtime SET state=%s,revision=revision+1,updated_at=%s "
                "WHERE workspace_id=%s AND runtime_id=%s AND revision=%s "
                "RETURNING workspace_id,runtime_id,account_id,strategy_version_id,mode,state,"
                "revision,updated_at",
                (
                    state,
                    updated_at,
                    runtime.workspace_id,
                    runtime.runtime_id,
                    expected_revision,
                ),
            ).fetchone()
            if row is None:
                raise PortError(ErrorCode.CONFLICT, "runtime changed during control")
            updated = _decode(row)
            append_in_transaction(conn, audit)
            return updated


def _decode(row: tuple[object, ...]) -> ExecutionRuntime:
    try:
        return ExecutionRuntime(
            cast(UUID, row[0]),
            cast(UUID, row[1]),
            cast(UUID, row[2]),
            cast(UUID, row[3]),
            cast(RuntimeMode, row[4]),
            cast(RuntimeState, row[5]),
            int(cast(int, row[6])),
            cast(datetime, row[7]),
        )
    except (TypeError, ValueError, PortError):
        raise PortError(ErrorCode.UNAVAILABLE, "invalid stored execution runtime") from None
