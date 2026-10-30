"""Immutable replay identity, dataset lineage and versioned transformation seams."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from qtrade_contracts import Bar, MarketDataset
from qtrade_contracts.models import BarGranularity, Granularity
from qtrade_domain.batches import ArchivedBatch
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class ReplayRequest:
    workspace_id: UUID
    dataset_id: UUID
    batch_ids: tuple[UUID, ...]
    transformation_version: str
    start: datetime
    end: datetime
    created_at: datetime
    parent_dataset_id: UUID | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.dataset_id, UUID)
            or not isinstance(self.batch_ids, tuple)
            or not self.batch_ids
            or any(not isinstance(item, UUID) for item in self.batch_ids)
            or len(set(self.batch_ids)) != len(self.batch_ids)
            or not isinstance(self.transformation_version, str)
            or not 1 <= len(self.transformation_version) <= 256
            or any(
                not isinstance(item, datetime) or item.utcoffset() is None
                for item in (self.start, self.end, self.created_at)
            )
            or self.start.astimezone(UTC) >= self.end.astimezone(UTC)
            or (
                self.parent_dataset_id is not None
                and (
                    not isinstance(self.parent_dataset_id, UUID)
                    or self.parent_dataset_id == self.dataset_id
                )
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid dataset replay request")
        for name in ("start", "end", "created_at"):
            object.__setattr__(self, name, getattr(self, name).astimezone(UTC))

    @property
    def fingerprint(self) -> str:
        payload = {
            "workspace_id": str(self.workspace_id),
            "dataset_id": str(self.dataset_id),
            "batch_ids": [str(item) for item in self.batch_ids],
            "transformation_version": self.transformation_version,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "created_at": self.created_at.isoformat(),
            "parent_dataset_id": str(self.parent_dataset_id) if self.parent_dataset_id else None,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True)
class PublishedDataset:
    metadata: MarketDataset
    request_sha256: str
    parent_dataset_id: UUID | None
    quality_report_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.metadata, MarketDataset)
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in (self.request_sha256, self.quality_report_sha256)
            )
            or (
                self.parent_dataset_id is not None
                and (
                    not isinstance(self.parent_dataset_id, UUID)
                    or self.parent_dataset_id == self.metadata.dataset_id
                )
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid published dataset")


class DatasetRepositoryPort(Protocol):
    def get(self, workspace_id: UUID, dataset_id: UUID) -> PublishedDataset: ...
    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[PublishedDataset, ...]: ...
    def for_instrument(
        self,
        workspace_id: UUID,
        instrument_id: str,
        *,
        granularity: Granularity | None = None,
        limit: int = 100,
        before: UUID | None = None,
    ) -> tuple[PublishedDataset, ...]: ...
    def put(self, actor_id: UUID, dataset: PublishedDataset) -> None: ...


class BarTransformationPort(Protocol):
    @property
    def version(self) -> str: ...
    def output_granularity(self, source: Granularity) -> BarGranularity: ...
    def transform(self, dataset_id: UUID, batches: tuple[ArchivedBatch, ...]) -> tuple[Bar, ...]:
        """Interpret verified raw records; never mutate the archived input or old datasets."""
        ...


class DatasetQualityPort(Protocol):
    transformation_version: str

    def assess(
        self, request: ReplayRequest, batches: tuple[ArchivedBatch, ...], bars: tuple[Bar, ...]
    ) -> dict[str, object]: ...
