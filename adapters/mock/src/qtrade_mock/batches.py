"""Immutable workspace-scoped metadata double; publication uses the shared service."""

from threading import RLock
from uuid import UUID

from qtrade_contracts import DataBatch
from qtrade_domain.ports import ErrorCode, PortError


class MockBatchRepository:
    def __init__(self) -> None:
        self._batches: dict[tuple[UUID, UUID], DataBatch] = {}
        self._lock = RLock()

    def put(self, actor_id: UUID, batch: DataBatch) -> None:
        with self._lock:
            key = (batch.workspace_id, batch.batch_id)
            previous = self._batches.get(key)
            if previous is not None and previous != batch:
                raise PortError(ErrorCode.CONFLICT, "batch metadata is immutable")
            self._batches[key] = batch

    def get(self, workspace_id: UUID, batch_id: UUID) -> DataBatch:
        with self._lock:
            try:
                return self._batches[(workspace_id, batch_id)]
            except KeyError:
                raise PortError(ErrorCode.NOT_FOUND, "batch unavailable") from None

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[DataBatch, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid batch page size")
        with self._lock:
            found = sorted(
                (batch for (scope, _), batch in self._batches.items() if scope == workspace_id),
                key=lambda batch: (batch.collected_at, str(batch.batch_id)),
                reverse=True,
            )
            return tuple(found[:limit])
