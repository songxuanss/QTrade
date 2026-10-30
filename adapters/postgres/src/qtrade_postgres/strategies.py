"""PostgreSQL adapter for workspace-scoped Strategy aggregates and frozen versions."""

import json
from dataclasses import replace
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from psycopg import Connection

from qtrade_contracts import StrategyVersion
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.strategies import (
    Strategy,
    StrategyStateTransition,
    StrategyVersionApproval,
    StrategyVersionLifecycle,
    StrategyVersionState,
)
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.query import column_query

_STRATEGY_COLUMNS = (
    "strategy_id,workspace_id,name,description,created_by,created_at,updated_at,"
    "current_version,archived"
)


class PostgresStrategyStore(PostgresStore):
    @staticmethod
    def _strategy(row: tuple[object, ...]) -> Strategy:
        try:
            return Strategy(
                UUID(str(row[0])),
                UUID(str(row[1])),
                str(row[2]),
                str(row[3]),
                UUID(str(row[4])),
                cast(datetime, row[5]),
                cast(datetime, row[6]),
                cast(int, row[7]),
                bool(row[8]),
            )
        except (TypeError, ValueError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored strategy") from None

    @staticmethod
    def _version(row: tuple[object, ...]) -> StrategyVersion:
        try:
            return StrategyVersion(
                schema_version=1,
                workspace_id=UUID(str(row[0])),
                strategy_id=UUID(str(row[1])),
                strategy_version_id=UUID(str(row[2])),
                version=cast(int, row[3]),
                created_by=UUID(str(row[4])),
                created_at=cast(datetime, row[5]),
                code_object_key=str(row[6]),
                code_sha256=str(row[7]),
                parameter_schema_json=json.dumps(row[8], sort_keys=True, separators=(",", ":")),
                dependencies_sha256=str(row[9]),
                dependencies_object_key=str(row[10]) if row[10] is not None else None,
            )
        except (TypeError, ValueError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored strategy version") from None

    @staticmethod
    def _version_columns() -> str:
        return (
            "workspace_id,strategy_id,strategy_version_id,version,created_by,created_at,"
            "code_object_key,code_sha256,parameter_schema,dependencies_sha256,dependencies_object_key"
        )

    def create(self, strategy: Strategy, version: StrategyVersion) -> None:
        if (strategy.strategy_id, strategy.workspace_id, strategy.current_version) != (
            version.strategy_id,
            version.workspace_id,
            1,
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid initial strategy version")
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO strategy "
                "(strategy_id,workspace_id,name,description,created_by,created_at,updated_at,"
                "current_version,archived) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,1,false)",
                (
                    strategy.strategy_id,
                    strategy.workspace_id,
                    strategy.name,
                    strategy.description,
                    strategy.created_by,
                    strategy.created_at,
                    strategy.updated_at,
                ),
            )
            self._insert_version(conn, version)

    def _insert_version(
        self, conn: Connection[tuple[object, ...]], version: StrategyVersion
    ) -> None:
        columns = self._version_columns()
        conn.execute(
            column_query(
                "INSERT INTO strategy_version ({}) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)",
                columns,
            ),
            (
                version.workspace_id,
                version.strategy_id,
                version.strategy_version_id,
                version.version,
                version.created_by,
                version.created_at,
                version.code_object_key,
                version.code_sha256,
                json.dumps(json.loads(version.parameter_schema_json)),
                version.dependencies_sha256,
                version.dependencies_object_key,
            ),
        )

    def get(self, workspace_id: UUID, strategy_id: UUID) -> Strategy | None:
        with self._connection() as conn:
            row = conn.execute(
                column_query(
                    "SELECT {} FROM strategy WHERE workspace_id=%s AND strategy_id=%s",
                    _STRATEGY_COLUMNS,
                ),
                (workspace_id, strategy_id),
            ).fetchone()
        return self._strategy(row) if row else None

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[Strategy, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy page size")
        with self._connection() as conn:
            rows = conn.execute(
                column_query(
                    "SELECT {} FROM strategy WHERE workspace_id=%s ORDER "
                    "BY updated_at DESC,strategy_id LIMIT %s",
                    _STRATEGY_COLUMNS,
                ),
                (workspace_id, limit),
            ).fetchall()
        return tuple(self._strategy(row) for row in rows)

    def versions(self, workspace_id: UUID, strategy_id: UUID) -> tuple[StrategyVersion, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                column_query(
                    "SELECT {} FROM strategy_version WHERE "
                    "workspace_id=%s AND strategy_id=%s ORDER BY version",
                    self._version_columns(),
                ),
                (workspace_id, strategy_id),
            ).fetchall()
        return tuple(self._version(row) for row in rows)

    def append_version(
        self,
        workspace_id: UUID,
        strategy_id: UUID,
        expected_version: int,
        strategy: Strategy,
        version: StrategyVersion,
    ) -> None:
        if (
            strategy.strategy_id,
            strategy.workspace_id,
            strategy.current_version,
            version.version,
            version.strategy_id,
            version.workspace_id,
        ) != (
            strategy_id,
            workspace_id,
            expected_version + 1,
            expected_version + 1,
            strategy_id,
            workspace_id,
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy version")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT current_version FROM strategy "
                "WHERE workspace_id=%s AND strategy_id=%s FOR UPDATE",
                (workspace_id, strategy_id),
            ).fetchone()
            if row is None:
                raise PortError(ErrorCode.NOT_FOUND, "strategy unavailable")
            if cast(int, row[0]) != expected_version:
                raise PortError(ErrorCode.CONFLICT, "strategy version changed")
            self._insert_version(conn, version)
            conn.execute(
                "UPDATE strategy SET current_version=%s,updated_at=%s "
                "WHERE workspace_id=%s AND strategy_id=%s",
                (strategy.current_version, strategy.updated_at, workspace_id, strategy_id),
            )

    @staticmethod
    def _state_name(value: object) -> StrategyVersionState:
        try:
            return StrategyVersionState(str(value))
        except ValueError:
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored strategy lifecycle") from None

    def _locked_version(
        self,
        conn: Connection[tuple[object, ...]],
        workspace_id: UUID,
        strategy_id: UUID,
        version: int,
    ) -> tuple[UUID, datetime, StrategyVersionState, datetime]:
        row = conn.execute(
            "SELECT sv.strategy_version_id,sv.created_at,COALESCE(l.state,'draft'),"
            "COALESCE(l.updated_at,sv.created_at) "
            "FROM strategy_version sv LEFT JOIN strategy_version_lifecycle l "
            "ON l.workspace_id=sv.workspace_id AND l.strategy_id=sv.strategy_id "
            "AND l.version=sv.version WHERE sv.workspace_id=%s AND sv.strategy_id=%s "
            "AND sv.version=%s FOR UPDATE OF sv",
            (workspace_id, strategy_id, version),
        ).fetchone()
        if row is None:
            raise PortError(ErrorCode.NOT_FOUND, "strategy version unavailable")
        return (
            UUID(str(row[0])),
            cast(datetime, row[1]),
            self._state_name(row[2]),
            cast(datetime, row[3]),
        )

    @staticmethod
    def _insert_transition(
        conn: Connection[tuple[object, ...]], event: StrategyStateTransition
    ) -> None:
        conn.execute(
            "INSERT INTO strategy_state_transition "
            "(transition_id,workspace_id,strategy_id,strategy_version_id,version,from_state,"
            "to_state,actor_id,occurred_at,reason,evidence) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (
                event.transition_id,
                event.workspace_id,
                event.strategy_id,
                event.strategy_version_id,
                event.version,
                event.from_state.value,
                event.to_state.value,
                event.actor_id,
                event.occurred_at,
                event.reason,
                event.evidence_json,
            ),
        )

    @staticmethod
    def _set_state(
        conn: Connection[tuple[object, ...]], lifecycle: StrategyVersionLifecycle
    ) -> None:
        conn.execute(
            "INSERT INTO strategy_version_lifecycle "
            "(workspace_id,strategy_id,version,state,updated_at) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (workspace_id,strategy_id,version) DO UPDATE "
            "SET state=EXCLUDED.state,updated_at=EXCLUDED.updated_at",
            (
                lifecycle.workspace_id,
                lifecycle.strategy_id,
                lifecycle.version,
                lifecycle.state.value,
                lifecycle.updated_at,
            ),
        )

    def state(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> StrategyVersionLifecycle:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT sv.strategy_version_id,COALESCE(l.state,'draft'),"
                "COALESCE(l.updated_at,sv.created_at) "
                "FROM strategy_version sv LEFT JOIN strategy_version_lifecycle l "
                "ON l.workspace_id=sv.workspace_id AND l.strategy_id=sv.strategy_id "
                "AND l.version=sv.version WHERE sv.workspace_id=%s AND sv.strategy_id=%s "
                "AND sv.version=%s",
                (workspace_id, strategy_id, version),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.NOT_FOUND, "strategy version unavailable")
        return StrategyVersionLifecycle(
            workspace_id,
            strategy_id,
            UUID(str(row[0])),
            version,
            self._state_name(row[1]),
            cast(datetime, row[2]),
        )

    def history(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> tuple[StrategyStateTransition, ...]:
        self.state(workspace_id, strategy_id, version)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT transition_id,strategy_version_id,from_state,to_state,actor_id,"
                "occurred_at,reason,evidence FROM strategy_state_transition "
                "WHERE workspace_id=%s AND strategy_id=%s AND version=%s "
                "ORDER BY occurred_at,transition_id",
                (workspace_id, strategy_id, version),
            ).fetchall()
        return tuple(
            StrategyStateTransition(
                UUID(str(row[0])),
                workspace_id,
                strategy_id,
                UUID(str(row[1])),
                version,
                self._state_name(row[2]),
                self._state_name(row[3]),
                UUID(str(row[4])),
                cast(datetime, row[5]),
                str(row[6]),
                json.dumps(row[7], sort_keys=True, separators=(",", ":")),
            )
            for row in rows
        )

    def approval(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> StrategyVersionApproval | None:
        self.state(workspace_id, strategy_id, version)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT approval_id,strategy_version_id,approver_id,approved_at,"
                "backtest_run_id,paper_observations,reason FROM strategy_version_approval "
                "WHERE workspace_id=%s AND strategy_id=%s AND version=%s",
                (workspace_id, strategy_id, version),
            ).fetchone()
        if row is None:
            return None
        return StrategyVersionApproval(
            UUID(str(row[0])),
            workspace_id,
            strategy_id,
            UUID(str(row[1])),
            version,
            UUID(str(row[2])),
            cast(datetime, row[3]),
            UUID(str(row[4])),
            cast(int, row[5]),
            str(row[6]),
        )

    def transition(
        self,
        expected_state: StrategyVersionState,
        lifecycle: StrategyVersionLifecycle,
        transition: StrategyStateTransition,
    ) -> StrategyVersionLifecycle:
        with self._connection() as conn:
            version_id, _, current_state, _ = self._locked_version(
                conn,
                lifecycle.workspace_id,
                lifecycle.strategy_id,
                lifecycle.version,
            )
            if current_state != expected_state:
                raise PortError(ErrorCode.CONFLICT, "strategy state changed")
            if (
                lifecycle.strategy_version_id != version_id
                or transition.strategy_version_id != version_id
                or transition.from_state != expected_state
                or transition.to_state != lifecycle.state
                or (
                    transition.workspace_id,
                    transition.strategy_id,
                    transition.version,
                )
                != (
                    lifecycle.workspace_id,
                    lifecycle.strategy_id,
                    lifecycle.version,
                )
                or transition.occurred_at != lifecycle.updated_at
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy transition")
            self._set_state(conn, lifecycle)
            self._insert_transition(conn, transition)
        return lifecycle

    def approve(
        self,
        expected_state: StrategyVersionState,
        lifecycle: StrategyVersionLifecycle,
        transition: StrategyStateTransition,
        approval: StrategyVersionApproval,
    ) -> StrategyVersionLifecycle:
        with self._connection() as conn:
            version_id, _, current_state, _ = self._locked_version(
                conn,
                lifecycle.workspace_id,
                lifecycle.strategy_id,
                lifecycle.version,
            )
            if current_state != expected_state:
                raise PortError(ErrorCode.CONFLICT, "strategy approval state changed")
            if (
                lifecycle.state != StrategyVersionState.APPROVED
                or lifecycle.strategy_version_id != version_id
                or approval.strategy_version_id != version_id
                or transition.strategy_version_id != version_id
                or transition.from_state != expected_state
                or transition.to_state != StrategyVersionState.APPROVED
                or (
                    transition.workspace_id,
                    transition.strategy_id,
                    transition.version,
                    transition.occurred_at,
                )
                != (
                    lifecycle.workspace_id,
                    lifecycle.strategy_id,
                    lifecycle.version,
                    lifecycle.updated_at,
                )
                or (
                    approval.workspace_id,
                    approval.strategy_id,
                    approval.strategy_version_id,
                    approval.version,
                    approval.approved_at,
                )
                != (
                    lifecycle.workspace_id,
                    lifecycle.strategy_id,
                    lifecycle.strategy_version_id,
                    lifecycle.version,
                    lifecycle.updated_at,
                )
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy approval")
            conn.execute(
                "INSERT INTO strategy_version_approval "
                "(approval_id,workspace_id,strategy_id,strategy_version_id,version,approver_id,"
                "approved_at,backtest_run_id,paper_observations,reason) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    approval.approval_id,
                    approval.workspace_id,
                    approval.strategy_id,
                    approval.strategy_version_id,
                    approval.version,
                    approval.approver_id,
                    approval.approved_at,
                    approval.backtest_run_id,
                    approval.paper_observations,
                    approval.reason,
                ),
            )
            self._set_state(conn, lifecycle)
            self._insert_transition(conn, transition)
        return lifecycle

    def archive_strategy(
        self, workspace_id: UUID, strategy_id: UUID, actor_id: UUID, now: datetime
    ) -> Strategy:
        with self._connection() as conn:
            row = conn.execute(
                column_query(
                    "SELECT {} FROM strategy WHERE workspace_id=%s AND strategy_id=%s FOR UPDATE",
                    _STRATEGY_COLUMNS,
                ),
                (workspace_id, strategy_id),
            ).fetchone()
            if row is None:
                raise PortError(ErrorCode.NOT_FOUND, "strategy unavailable")
            strategy = self._strategy(row)
            versions = conn.execute(
                "SELECT sv.version,sv.strategy_version_id,sv.created_at,"
                "COALESCE(l.state,'draft') FROM strategy_version sv "
                "LEFT JOIN strategy_version_lifecycle l ON l.workspace_id=sv.workspace_id "
                "AND l.strategy_id=sv.strategy_id AND l.version=sv.version "
                "WHERE sv.workspace_id=%s AND sv.strategy_id=%s ORDER BY sv.version "
                "FOR UPDATE OF sv",
                (workspace_id, strategy_id),
            ).fetchall()
            states = [
                (cast(int, item[0]), self._state_name(item[3]), UUID(str(item[1])))
                for item in versions
            ]
            if any(state == StrategyVersionState.LIVE for _, state, _ in states):
                raise PortError(ErrorCode.REJECTED, "pause live versions before archive")
            for number, current_state, version_id in states:
                if current_state == StrategyVersionState.ARCHIVED:
                    continue
                lifecycle = StrategyVersionLifecycle(
                    workspace_id,
                    strategy_id,
                    version_id,
                    number,
                    StrategyVersionState.ARCHIVED,
                    now,
                )
                event = StrategyStateTransition(
                    uuid4(),
                    workspace_id,
                    strategy_id,
                    version_id,
                    number,
                    current_state,
                    StrategyVersionState.ARCHIVED,
                    actor_id,
                    now,
                    "strategy archive",
                    '{"source":"strategy_archive"}',
                )
                self._set_state(conn, lifecycle)
                self._insert_transition(conn, event)
            if not strategy.archived:
                conn.execute(
                    "UPDATE strategy SET archived=true,updated_at=%s "
                    "WHERE workspace_id=%s AND strategy_id=%s",
                    (now, workspace_id, strategy_id),
                )
            return replace(strategy, archived=True, updated_at=now)
