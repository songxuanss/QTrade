"""PostgreSQL adapter for durable walk-forward batches and their window runs."""

import json
from typing import cast
from uuid import UUID

from qtrade_contracts import BacktestWalkForwardBatch
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.query import column_query

_COLUMNS = "workspace_id,walk_forward_id,revision,payload"


class PostgresWalkForwardStore(PostgresStore):
    """Batches are immutable: window outcomes are re-derived from their runs."""

    @staticmethod
    def _batch(row: tuple[object, ...]) -> BacktestWalkForwardBatch:
        try:
            workspace_id = UUID(str(row[0]))
            walk_forward_id = UUID(str(row[1]))
            batch = BacktestWalkForwardBatch.model_validate_json(json.dumps(row[3]))
            if (batch.workspace_id, batch.walk_forward_id) != (
                workspace_id,
                walk_forward_id,
            ):
                raise ValueError
            if cast(int, row[2]) != 1:
                raise ValueError
            return batch
        except (TypeError, ValueError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored walk-forward batch") from None

    def create(self, batch: BacktestWalkForwardBatch) -> None:
        if len({item.index for item in batch.windows}) != len(batch.windows):
            raise PortError(ErrorCode.INVALID_INPUT, "duplicate walk-forward window index")
        with self._connection() as conn:
            version = conn.execute(
                "SELECT strategy_version_id FROM strategy_version WHERE workspace_id=%s "
                "AND strategy_id=%s AND version=%s",
                (batch.workspace_id, batch.strategy_id, batch.strategy_version),
            ).fetchone()
            if version is None or UUID(str(version[0])) != batch.strategy_version_id:
                raise PortError(ErrorCode.NOT_FOUND, "strategy version unavailable")
            conn.execute(
                "INSERT INTO backtest_walk_forward(workspace_id,walk_forward_id,created_at,"
                "created_by,strategy_id,strategy_version,strategy_version_id,dataset_id,"
                "instrument_id,revision,payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s::jsonb)",
                (
                    batch.workspace_id,
                    batch.walk_forward_id,
                    batch.created_at,
                    batch.created_by,
                    batch.strategy_id,
                    batch.strategy_version,
                    batch.strategy_version_id,
                    batch.dataset_id,
                    batch.instrument_id,
                    batch.model_dump_json(),
                ),
            )

    def get(self, workspace_id: UUID, walk_forward_id: UUID) -> BacktestWalkForwardBatch:
        with self._connection() as conn:
            row = conn.execute(
                column_query(
                    "SELECT {} FROM backtest_walk_forward WHERE workspace_id=%s "
                    "AND walk_forward_id=%s",
                    _COLUMNS,
                ),
                (workspace_id, walk_forward_id),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.NOT_FOUND, "walk-forward batch unavailable")
        return self._batch(row)

    def list(
        self, workspace_id: UUID, limit: int = 50
    ) -> tuple[BacktestWalkForwardBatch, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid walk-forward page size")
        with self._connection() as conn:
            rows = conn.execute(
                column_query(
                    "SELECT {} FROM backtest_walk_forward WHERE workspace_id=%s "
                    "ORDER BY created_at DESC,walk_forward_id DESC LIMIT %s",
                    _COLUMNS,
                ),
                (workspace_id, limit),
            ).fetchall()
        return tuple(self._batch(row) for row in rows)
