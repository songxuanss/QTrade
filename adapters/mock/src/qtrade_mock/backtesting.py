"""In-memory run adapter for application and browser scenarios."""

from threading import RLock
from uuid import UUID

from qtrade_domain.backtesting import BacktestRunEvent, BacktestRunRecord
from qtrade_domain.ports import ErrorCode, PortError

_TERMINAL = {"cancelled", "failed", "completed"}
_TRANSITIONS = {
    "queued": {"queued", "running", "cancelled", "failed", "completed"},
    "running": {"running", "cancelled", "failed", "completed"},
    "cancelled": set(),
    "failed": set(),
    "completed": set(),
}


class MockBacktestRunStore:
    def __init__(self) -> None:
        self._records: dict[tuple[UUID, UUID], BacktestRunRecord] = {}
        self._events: dict[tuple[UUID, UUID], list[BacktestRunEvent]] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._events.clear()

    def create(self, record: BacktestRunRecord, event: BacktestRunEvent) -> None:
        run = record.run
        key = (run.workspace_id, run.run_id)
        if (
            record.revision != 1
            or record.engine_job_id is not None
            or run.status != "queued"
            or run.progress != 0
            or (event.workspace_id, event.run_id, event.status, event.progress)
            != (run.workspace_id, run.run_id, run.status, run.progress)
            or event.revision != record.revision
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid initial backtest run")
        with self._lock:
            if key in self._records:
                raise PortError(ErrorCode.CONFLICT, "backtest run already exists")
            self._records[key] = record
            self._events[key] = [event]

    def get(self, workspace_id: UUID, run_id: UUID) -> BacktestRunRecord:
        with self._lock:
            try:
                return self._records[(workspace_id, run_id)]
            except KeyError:
                raise PortError(ErrorCode.NOT_FOUND, "backtest run unavailable") from None

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[BacktestRunRecord, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest page size")
        with self._lock:
            rows = [record for (scope, _), record in self._records.items() if scope == workspace_id]
            return tuple(
                sorted(
                    rows, key=lambda item: (item.run.created_at, str(item.run.run_id)), reverse=True
                )[:limit]
            )

    def history(self, workspace_id: UUID, run_id: UUID) -> tuple[BacktestRunEvent, ...]:
        self.get(workspace_id, run_id)
        with self._lock:
            return tuple(self._events[(workspace_id, run_id)])

    def update(
        self,
        expected_revision: int,
        record: BacktestRunRecord,
        event: BacktestRunEvent | None,
    ) -> BacktestRunRecord:
        run = record.run
        key = (run.workspace_id, run.run_id)
        with self._lock:
            current = self.get(*key)
            if current.revision != expected_revision:
                raise PortError(ErrorCode.CONFLICT, "backtest run changed")
            before, after = current.run, run
            immutable_before = before.model_dump(
                exclude={"status", "progress", "result_object_key", "error_code"}
            )
            immutable_after = after.model_dump(
                exclude={"status", "progress", "result_object_key", "error_code"}
            )
            if (
                immutable_before != immutable_after
                or record.revision != current.revision + 1
                or current.engine_job_id is not None
                and record.engine_job_id != current.engine_job_id
                or after.status not in _TRANSITIONS[before.status]
                or after.progress < before.progress
                or (
                    event is None
                    and (after.status, after.progress) != (before.status, before.progress)
                )
                or (
                    event is not None
                    and (
                        (event.workspace_id, event.run_id, event.status, event.progress)
                        != (after.workspace_id, after.run_id, after.status, after.progress)
                    )
                )
                or (event is not None and event.revision != record.revision)
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest run update")
            self._records[key] = record
            if event is not None:
                self._events[key].append(event)
            return record
