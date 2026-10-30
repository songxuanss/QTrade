"""Immutable dataset publication double with lineage checks under one lock."""

from threading import RLock
from uuid import UUID

from qtrade_contracts.models import Granularity
from qtrade_domain.datasets import PublishedDataset
from qtrade_domain.ports import ErrorCode, PortError


class MockDatasetRepository:
    def __init__(self) -> None:
        self._datasets: dict[tuple[UUID, UUID], PublishedDataset] = {}
        self._lock = RLock()

    def get(self, workspace_id: UUID, dataset_id: UUID) -> PublishedDataset:
        with self._lock:
            try:
                return self._datasets[(workspace_id, dataset_id)]
            except KeyError:
                raise PortError(ErrorCode.NOT_FOUND, "dataset unavailable") from None

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[PublishedDataset, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid dataset page size")
        with self._lock:
            datasets = sorted(
                (
                    dataset
                    for (scope, _), dataset in self._datasets.items()
                    if scope == workspace_id
                ),
                key=lambda dataset: (dataset.metadata.created_at, str(dataset.metadata.dataset_id)),
                reverse=True,
            )
            return tuple(datasets[:limit])

    def for_instrument(
        self,
        workspace_id: UUID,
        instrument_id: str,
        *,
        granularity: Granularity | None = None,
        limit: int = 100,
        before: UUID | None = None,
    ) -> tuple[PublishedDataset, ...]:
        if (
            not isinstance(instrument_id, str)
            or not 1 <= len(instrument_id) <= 256
            or type(limit) is not int
            or not 1 <= limit <= 200
            or granularity not in (None, "tick", "1m", "5m", "15m", "1h", "1d")
            or (before is not None and not isinstance(before, UUID))
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid instrument dataset query")
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for (scope, identity), item in self._datasets.items()
                        if scope == workspace_id
                        and instrument_id in item.metadata.instrument_ids
                        and (granularity is None or item.metadata.granularity == granularity)
                        and (before is None or identity.int < before.int)
                    ),
                    key=lambda item: item.metadata.dataset_id.int,
                    reverse=True,
                )[:limit]
            )

    def put(self, actor_id: UUID, dataset: PublishedDataset) -> None:
        with self._lock:
            item = dataset.metadata
            key = (item.workspace_id, item.dataset_id)
            previous = self._datasets.get(key)
            if previous is not None:
                if previous != dataset:
                    raise PortError(ErrorCode.CONFLICT, "dataset metadata is immutable")
                return
            if dataset.parent_dataset_id is None:
                if item.version != 1:
                    raise PortError(ErrorCode.INVALID_INPUT, "invalid initial dataset version")
            else:
                parent = self.get(item.workspace_id, dataset.parent_dataset_id).metadata
                if item.version != parent.version + 1 or item.batch_ids != parent.batch_ids:
                    raise PortError(ErrorCode.INVALID_INPUT, "invalid dataset replay lineage")
            self._datasets[key] = dataset
