"""Raw collection identity and immutable batch metadata persistence boundary."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from qtrade_contracts import DataBatch
from qtrade_contracts.models import Granularity
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord, validate_records


@dataclass(frozen=True)
class BatchInput:
    workspace_id: UUID
    batch_id: UUID
    source: str
    authorization_id: str
    ingestion_task_id: UUID
    instrument_id: str
    granularity: Granularity
    collected_at: datetime
    records: tuple[RawRecord, ...]

    def __post_init__(self) -> None:
        validate_records(self.records)
        self.metadata(object_key="pending/raw.parquet", content_sha256="0" * 64)

    def metadata(self, *, object_key: str, content_sha256: str) -> DataBatch:
        try:
            result = DataBatch(
                schema_version=1,
                workspace_id=self.workspace_id,
                batch_id=self.batch_id,
                source=self.source,
                authorization_id=self.authorization_id,
                ingestion_task_id=self.ingestion_task_id,
                instrument_id=self.instrument_id,
                granularity=self.granularity,
                collected_at=self.collected_at,
                object_key=object_key,
                content_sha256=content_sha256,
                record_count=len(self.records),
            )
            return result.model_copy(update={"collected_at": result.collected_at.astimezone(UTC)})
        except (ValueError, TypeError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid batch collection metadata") from None


@dataclass(frozen=True)
class ArchivedBatch:
    metadata: DataBatch
    records: tuple[RawRecord, ...]

    def __post_init__(self) -> None:
        validate_records(self.records)
        if (
            not isinstance(self.metadata, DataBatch)
            or len(self.records) != self.metadata.record_count
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid archived batch")


class BatchRepositoryPort(Protocol):
    def put(self, actor_id: UUID, batch: DataBatch) -> None: ...
    def get(self, workspace_id: UUID, batch_id: UUID) -> DataBatch: ...
    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[DataBatch, ...]: ...
