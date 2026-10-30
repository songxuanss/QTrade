"""Durable workspace-scoped market stream health snapshots."""

from datetime import datetime
from typing import cast
from uuid import UUID

import psycopg

from qtrade_domain.authorization import Permission
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.realtime import (
    ConnectionStatus,
    MarketStreamHealth,
    MarketStreamKey,
)
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.query import column_query
from qtrade_postgres.workspaces import PostgresWorkspaceStore

_COLUMNS = """workspace_id,source,instrument_id,status,updated_at,heartbeat_at,last_event_at,
last_sequence,latest_lag_ms,max_lag_ms,out_of_order_count,reconnect_count,next_reconnect_at,
last_error_code"""


class PostgresRealtimeHealthStore(PostgresStore):
    @staticmethod
    def _decode(row: tuple[object, ...]) -> MarketStreamHealth:
        try:
            return MarketStreamHealth(
                MarketStreamKey(UUID(str(row[0])), str(row[1]), str(row[2])),
                cast(ConnectionStatus, str(row[3])),
                cast(datetime, row[4]),
                cast(datetime | None, row[5]),
                cast(datetime | None, row[6]),
                cast(int | None, row[7]),
                cast(int | None, row[8]),
                cast(int, row[9]),
                cast(int, row[10]),
                cast(int, row[11]),
                cast(datetime | None, row[12]),
                ErrorCode(str(row[13])) if row[13] is not None else None,
            )
        except (TypeError, ValueError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored market health state") from None

    @staticmethod
    def _authorize(
        conn: psycopg.Connection[tuple[object, ...]], actor_id: UUID, workspace_id: UUID
    ) -> None:
        conn.execute(
            "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE", (workspace_id,)
        )
        access = PostgresWorkspaceStore._access(conn, actor_id, workspace_id)
        if access is None:
            raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
        access.require(Permission.DATA_MANAGE)

    def get(self, key: MarketStreamKey) -> MarketStreamHealth | None:
        with self._connection() as conn:
            row = conn.execute(
                column_query(
                    "SELECT {} FROM market_stream_health WHERE "
                    "workspace_id=%s AND source=%s AND instrument_id=%s",
                    _COLUMNS,
                ),
                (key.workspace_id, key.source, key.instrument_id),
            ).fetchone()
        return self._decode(row) if row is not None else None

    def put(self, actor_id: UUID, health: MarketStreamHealth) -> None:
        with self._connection() as conn:
            self._authorize(conn, actor_id, health.key.workspace_id)
            conn.execute(
                """INSERT INTO market_stream_health(
                workspace_id,source,instrument_id,status,updated_at,heartbeat_at,last_event_at,
                last_sequence,latest_lag_ms,max_lag_ms,out_of_order_count,reconnect_count,
                next_reconnect_at,last_error_code)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(workspace_id,source,instrument_id) DO UPDATE SET
                status=EXCLUDED.status,updated_at=EXCLUDED.updated_at,
                heartbeat_at=EXCLUDED.heartbeat_at,last_event_at=EXCLUDED.last_event_at,
                last_sequence=EXCLUDED.last_sequence,latest_lag_ms=EXCLUDED.latest_lag_ms,
                max_lag_ms=EXCLUDED.max_lag_ms,out_of_order_count=EXCLUDED.out_of_order_count,
                reconnect_count=EXCLUDED.reconnect_count,next_reconnect_at=EXCLUDED.next_reconnect_at,
                last_error_code=EXCLUDED.last_error_code""",
                (
                    health.key.workspace_id,
                    health.key.source,
                    health.key.instrument_id,
                    health.status,
                    health.updated_at,
                    health.heartbeat_at,
                    health.last_event_at,
                    health.last_sequence,
                    health.latest_lag_ms,
                    health.max_lag_ms,
                    health.out_of_order_count,
                    health.reconnect_count,
                    health.next_reconnect_at,
                    health.last_error_code.value if health.last_error_code else None,
                ),
            )

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[MarketStreamHealth, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid market health page size")
        with self._connection() as conn:
            rows = conn.execute(
                column_query(
                    "SELECT {} FROM market_stream_health WHERE "
                    "workspace_id=%s ORDER BY source,instrument_id LIMIT "
                    "%s",
                    _COLUMNS,
                ),
                (workspace_id, limit),
            ).fetchall()
        return tuple(self._decode(row) for row in rows)
