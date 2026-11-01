"""PostgreSQL adapter for immutable Backtest Run inputs and CAS progress."""

import json
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from psycopg import Connection

from qtrade_contracts import BacktestRun
from qtrade_domain.backtesting import BacktestRunEvent, BacktestRunRecord
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.query import column_query

_RUN_COLUMNS = "workspace_id,run_id,engine_job_id,revision,payload"
_TERMINAL = {"cancelled", "failed", "completed"}
_TRANSITIONS = {
    "queued": {"queued", "running", "cancelled", "failed", "completed"},
    "running": {"running", "cancelled", "failed", "completed"},
    "cancelled": set(),
    "failed": set(),
    "completed": set(),
}


class PostgresBacktestRunStore(PostgresStore):
    @staticmethod
    def _record(row: tuple[object, ...]) -> BacktestRunRecord:
        try:
            workspace_id = UUID(str(row[0]))
            run_id = UUID(str(row[1]))
            engine_job_id = UUID(str(row[2])) if row[2] is not None else None
            revision = cast(int, row[3])
            run = BacktestRun.model_validate_json(json.dumps(row[4]))
            if (run.workspace_id, run.run_id) != (workspace_id, run_id):
                raise ValueError
            return BacktestRunRecord(run, engine_job_id, revision)
        except (TypeError, ValueError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored backtest run") from None

    @staticmethod
    def _insert_event(conn: Connection[tuple[object, ...]], event: BacktestRunEvent) -> None:
        conn.execute(
            "INSERT INTO backtest_run_event(event_id,workspace_id,run_id,revision,status,"
            "progress,occurred_at,error_code) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                event.event_id,
                event.workspace_id,
                event.run_id,
                event.revision,
                event.status,
                event.progress,
                event.occurred_at,
                event.error_code,
            ),
        )

    def create(self, record: BacktestRunRecord, event: BacktestRunEvent) -> None:
        run = record.run
        with self._connection() as conn:
            strategy = conn.execute(
                "SELECT strategy_id,version FROM strategy_version WHERE workspace_id=%s "
                "AND strategy_version_id=%s",
                (run.workspace_id, run.strategy_version_id),
            ).fetchone()
            if strategy is None:
                raise PortError(ErrorCode.NOT_FOUND, "strategy version unavailable")
            if (
                record.revision != 1
                or record.engine_job_id is not None
                or (
                    run.status,
                    run.progress,
                    event.workspace_id,
                    event.run_id,
                    event.status,
                    event.progress,
                )
                != ("queued", 0, run.workspace_id, run.run_id, "queued", 0)
                or event.revision != record.revision
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid initial backtest run")
            conn.execute(
                "INSERT INTO backtest_run(workspace_id,run_id,created_at,created_by,strategy_id,"
                "strategy_version,strategy_version_id,dataset_id,manifest_id,engine_job_id,"
                "revision,status,progress,payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,1,%s,%s,%s::jsonb)",
                (
                    run.workspace_id,
                    run.run_id,
                    run.created_at,
                    run.created_by,
                    strategy[0],
                    strategy[1],
                    run.strategy_version_id,
                    run.dataset_id,
                    run.manifest_id,
                    run.status,
                    run.progress,
                    run.model_dump_json(),
                ),
            )
            self._insert_event(conn, event)

    def claim(
        self, worker_id: UUID, now: datetime, lease_seconds: int, max_attempts: int
    ) -> BacktestRunRecord | None:
        """Atomically lease one queued or expired run for a worker."""
        if (
            not isinstance(worker_id, UUID)
            or now.utcoffset() is None
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 3600
            or type(max_attempts) is not int
            or not 1 <= max_attempts <= 100
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest lease")
        expires = now + timedelta(seconds=lease_seconds)
        with self._connection() as conn:
            row = conn.execute(
                """WITH candidate AS (
                    SELECT workspace_id,run_id FROM backtest_run
                    WHERE cancel_requested=false
                      AND status='queued'
                      AND attempts < %s
                    ORDER BY attempts,created_at,run_id
                    FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE backtest_run b
                SET status='running', progress=GREATEST(b.progress,10),
                    lease_owner=%s, lease_expires_at=%s, attempts=b.attempts+1,
                    revision=b.revision+1,
                    payload=jsonb_set(
                        jsonb_set(b.payload,'{status}','"running"'),
                        '{progress}',to_jsonb(GREATEST(b.progress,10))
                    )
                FROM candidate c
                WHERE b.workspace_id=c.workspace_id AND b.run_id=c.run_id
                RETURNING b.workspace_id,b.run_id,b.engine_job_id,b.revision,b.payload""",
                (max_attempts, worker_id, expires),
            ).fetchone()
            if row is None:
                return None
            record = self._record(row)
            self._insert_event(
                conn,
                BacktestRunEvent(
                    uuid4(),
                    record.run.workspace_id,
                    record.run.run_id,
                    "running",
                    record.run.progress,
                    now,
                    revision=record.revision,
                ),
            )
            return record

    def renew(
        self,
        workspace_id: UUID,
        run_id: UUID,
        worker_id: UUID,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        """Extend a live lease; False means this worker no longer owns the run."""
        if (
            not isinstance(worker_id, UUID)
            or now.utcoffset() is None
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 3600
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest lease renewal")
        with self._connection() as conn:
            row = conn.execute(
                """UPDATE backtest_run SET lease_expires_at=%s, revision=revision+1
                WHERE workspace_id=%s AND run_id=%s AND status='running'
                  AND lease_owner=%s
                RETURNING run_id""",
                (now + timedelta(seconds=lease_seconds), workspace_id, run_id, worker_id),
            ).fetchone()
        return row is not None

    def request_cancel(self, workspace_id: UUID, run_id: UUID, now: datetime) -> BacktestRunRecord:
        """Cancel a queued/running run and prevent a leased worker from publishing."""
        if now.utcoffset() is None:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid cancellation time")
        with self._connection() as conn:
            row = conn.execute(
                """UPDATE backtest_run
                SET status='cancelled', cancel_requested=true,
                    lease_owner=NULL, lease_expires_at=NULL, revision=revision+1,
                    payload=jsonb_set(payload,'{status}','"cancelled"')
                WHERE workspace_id=%s AND run_id=%s AND status IN ('queued','running')
                RETURNING workspace_id,run_id,engine_job_id,revision,payload""",
                (workspace_id, run_id),
            ).fetchone()
            if row is None:
                current = self.get(workspace_id, run_id)
                if current.run.status in _TERMINAL:
                    raise PortError(ErrorCode.CONFLICT, "backtest run is already terminal")
                raise PortError(ErrorCode.CONFLICT, "backtest run cannot be cancelled")
            record = self._record(row)
            self._insert_event(
                conn,
                BacktestRunEvent(
                    uuid4(),
                    workspace_id,
                    run_id,
                    "cancelled",
                    record.run.progress,
                    now,
                    revision=record.revision,
                ),
            )
            return record

    def requeue_expired(self, now: datetime, max_attempts: int) -> tuple[int, int]:
        """Requeue expired leases; fail only after the retry budget is spent.

        Returns (requeued, failed). Requeued runs keep their attempts count and
        become claimable again, so a killed Worker loses no work.
        """
        if now.utcoffset() is None or type(max_attempts) is not int or max_attempts < 1:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid lease expiry sweep")
        with self._connection() as conn:
            rows = conn.execute(
                """WITH expired AS (
                    SELECT workspace_id,run_id FROM backtest_run
                    WHERE status='running' AND lease_expires_at < %s
                    ORDER BY lease_expires_at
                    FOR UPDATE SKIP LOCKED
                ), requeued AS (
                    UPDATE backtest_run b
                    SET status='queued', lease_owner=NULL, lease_expires_at=NULL,
                        revision=b.revision+1,
                        payload=jsonb_set(b.payload,'{status}','"queued"')
                    FROM expired e
                    WHERE b.workspace_id=e.workspace_id AND b.run_id=e.run_id
                      AND b.attempts < %s
                    RETURNING b.workspace_id,b.run_id,b.revision,b.progress
                ), failed AS (
                    UPDATE backtest_run b
                    SET status='failed', lease_owner=NULL, lease_expires_at=NULL,
                        revision=b.revision+1,
                        payload=jsonb_set(
                            jsonb_set(b.payload,'{status}','"failed"'),
                            '{error_code}','"lease_expired"'
                        )
                    FROM expired e
                    WHERE b.workspace_id=e.workspace_id AND b.run_id=e.run_id
                      AND b.attempts >= %s
                    RETURNING b.workspace_id,b.run_id,b.revision,b.progress
                )
                SELECT 'requeued',workspace_id,run_id,revision,progress FROM requeued
                UNION ALL
                SELECT 'failed',workspace_id,run_id,revision,progress FROM failed""",
                (now, max_attempts, max_attempts),
            ).fetchall()
            for row in rows:
                self._insert_event(
                    conn,
                    BacktestRunEvent(
                        uuid4(),
                        UUID(str(row[1])),
                        UUID(str(row[2])),
                        "queued" if row[0] == "requeued" else "failed",
                        cast(int, row[4]),
                        now,
                        None if row[0] == "requeued" else "lease_expired",
                        cast(int, row[3]),
                    ),
                )
        requeued = sum(1 for row in rows if row[0] == "requeued")
        return requeued, len(rows) - requeued

    def get(self, workspace_id: UUID, run_id: UUID) -> BacktestRunRecord:
        with self._connection() as conn:
            row = conn.execute(
                column_query(
                    "SELECT {} FROM backtest_run WHERE workspace_id=%s AND run_id=%s", _RUN_COLUMNS
                ),
                (workspace_id, run_id),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.NOT_FOUND, "backtest run unavailable")
        return self._record(row)

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[BacktestRunRecord, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest page size")
        with self._connection() as conn:
            rows = conn.execute(
                column_query(
                    "SELECT {} FROM backtest_run WHERE workspace_id=%s "
                    "ORDER BY created_at DESC,run_id DESC LIMIT %s",
                    _RUN_COLUMNS,
                ),
                (workspace_id, limit),
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def history(self, workspace_id: UUID, run_id: UUID) -> tuple[BacktestRunEvent, ...]:
        self.get(workspace_id, run_id)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT event_id,status,progress,occurred_at,error_code,revision "
                "FROM backtest_run_event WHERE workspace_id=%s AND run_id=%s ORDER BY revision",
                (workspace_id, run_id),
            ).fetchall()
        return tuple(
            BacktestRunEvent(
                UUID(str(row[0])),
                workspace_id,
                run_id,
                str(row[1]),
                cast(int, row[2]),
                cast(datetime, row[3]),
                str(row[4]) if row[4] is not None else None,
                cast(int, row[5]),
            )
            for row in rows
        )

    def update(
        self,
        expected_revision: int,
        record: BacktestRunRecord,
        event: BacktestRunEvent | None,
    ) -> BacktestRunRecord:
        run = record.run
        with self._connection() as conn:
            row = conn.execute(
                column_query(
                    "SELECT {} FROM backtest_run WHERE workspace_id=%s AND run_id=%s FOR UPDATE",
                    _RUN_COLUMNS,
                ),
                (run.workspace_id, run.run_id),
            ).fetchone()
            if row is None:
                raise PortError(ErrorCode.NOT_FOUND, "backtest run unavailable")
            current = self._record(row)
            before = current.run
            if current.revision != expected_revision:
                raise PortError(ErrorCode.CONFLICT, "backtest run changed")
            immutable_before = before.model_dump(
                exclude={"status", "progress", "result_object_key", "error_code"}
            )
            immutable_after = run.model_dump(
                exclude={"status", "progress", "result_object_key", "error_code"}
            )
            if (
                immutable_before != immutable_after
                or record.revision != current.revision + 1
                or (
                    current.engine_job_id is not None
                    and record.engine_job_id != current.engine_job_id
                )
                or run.status not in _TRANSITIONS[before.status]
                or run.progress < before.progress
                or (
                    event is None and (run.status, run.progress) != (before.status, before.progress)
                )
                or (
                    event is not None
                    and (
                        (
                            event.workspace_id,
                            event.run_id,
                            event.status,
                            event.progress,
                        )
                        != (run.workspace_id, run.run_id, run.status, run.progress)
                        or event.revision != record.revision
                    )
                )
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest run update")
            conn.execute(
                "UPDATE backtest_run SET engine_job_id=%s,revision=%s,status=%s,progress=%s,"
                "lease_owner=CASE WHEN %s IN ('completed','failed','cancelled') THEN NULL "
                "ELSE lease_owner END,"
                "lease_expires_at=CASE WHEN %s IN ('completed','failed','cancelled') THEN NULL "
                "ELSE lease_expires_at END,"
                "payload=%s::jsonb WHERE workspace_id=%s AND run_id=%s",
                (
                    record.engine_job_id,
                    record.revision,
                    run.status,
                    run.progress,
                    run.status,
                    run.status,
                    run.model_dump_json(),
                    run.workspace_id,
                    run.run_id,
                ),
            )
            if event is not None:
                self._insert_event(conn, event)
        return record
