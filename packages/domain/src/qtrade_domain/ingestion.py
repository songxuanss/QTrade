"""Historical collection state, fenced checkpoints and provider-neutral scheduling."""

import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal, Protocol, get_args
from uuid import UUID, uuid5

from qtrade_contracts.models import Granularity
from qtrade_domain.batches import BatchInput
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord, validate_records

Status = Literal["queued", "running", "waiting", "failed", "completed"]


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PortError(ErrorCode.INVALID_INPUT, "aware ingestion timestamp required")
    return value.astimezone(UTC)


def cursor_valid(value: str | None) -> bool:
    return value is None or (isinstance(value, str) and 1 <= len(value) <= 2048)


@dataclass(frozen=True)
class CollectionRequest:
    workspace_id: UUID
    task_id: UUID
    source: str
    authorization_id: str
    instrument_id: str
    granularity: Granularity
    start: datetime
    end: datetime
    created_at: datetime
    transformation_version: str
    page_size: int = 1000
    download_group_id: UUID | None = None
    part_index: int = 1
    part_count: int = 1

    def __post_init__(self) -> None:
        for field in ("start", "end", "created_at"):
            object.__setattr__(self, field, utc(getattr(self, field)))
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.task_id, UUID)
            or not isinstance(self.source, str)
            or not 1 <= len(self.source) <= 256
            or not isinstance(self.authorization_id, str)
            or not 1 <= len(self.authorization_id) <= 256
            or not isinstance(self.instrument_id, str)
            or not 1 <= len(self.instrument_id) <= 256
            or self.start >= self.end
            or self.granularity not in get_args(Granularity)
            or type(self.page_size) is not int
            or not 1 <= self.page_size <= 10000
            or not isinstance(self.transformation_version, str)
            or not 1 <= len(self.transformation_version) <= 256
            or (self.download_group_id is not None and not isinstance(self.download_group_id, UUID))
            or type(self.part_index) is not int
            or type(self.part_count) is not int
            or not 1 <= self.part_index <= self.part_count
            or (self.download_group_id is None and self.part_count != 1)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid historical collection request")
        self.batch(0, self.created_at, ())  # Shared validation of source/scope/instrument/license.

    @property
    def dataset_id(self) -> UUID:
        return uuid5(self.task_id, "market-dataset.v1")

    def batch_id(self, page_index: int) -> UUID:
        if type(page_index) is not int or page_index < 0:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid collection page index")
        return uuid5(self.task_id, f"raw-page.v1:{page_index}")

    def batch(
        self, page_index: int, collected_at: datetime, records: tuple[RawRecord, ...]
    ) -> BatchInput:
        return BatchInput(
            self.workspace_id,
            self.batch_id(page_index),
            self.source,
            self.authorization_id,
            self.task_id,
            self.instrument_id,
            self.granularity,
            collected_at,
            records,
        )


@dataclass(frozen=True)
class ProviderPage:
    records: tuple[RawRecord, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        validate_records(self.records)
        if (
            not cursor_valid(self.next_cursor)
            or len(self.records) > 10000
            or sum(len(record.payload) for record in self.records) > 16_000_000
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid provider continuation cursor")


@dataclass(frozen=True)
class StagedPage:
    page: ProviderPage
    collected_at: datetime
    continuation_repeated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.page, ProviderPage):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid staged collection page")
        if type(self.continuation_repeated) is not bool:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid staged collection state")
        object.__setattr__(self, "collected_at", utc(self.collected_at))


@dataclass(frozen=True)
class IngestionTask:
    request: CollectionRequest
    actor_id: UUID
    status: Status
    next_attempt_at: datetime
    cursor: str | None = None
    batch_ids: tuple[UUID, ...] = ()
    pending: StagedPage | None = None
    collection_complete: bool = False
    failures: int = 0
    last_error_code: ErrorCode | None = None
    dataset_id: UUID | None = None
    seen_cursor_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "next_attempt_at", utc(self.next_attempt_at))
        if (
            not isinstance(self.request, CollectionRequest)
            or not isinstance(self.actor_id, UUID)
            or self.status not in get_args(Status)
            or not cursor_valid(self.cursor)
            or not isinstance(self.batch_ids, tuple)
            or any(
                item != self.request.batch_id(index) for index, item in enumerate(self.batch_ids)
            )
            or (self.pending is not None and not isinstance(self.pending, StagedPage))
            or type(self.collection_complete) is not bool
            or type(self.failures) is not int
            or self.failures < 0
            or not isinstance(self.seen_cursor_hashes, tuple)
            or any(
                not isinstance(item, str)
                or len(item) != 64
                or any(char not in "0123456789abcdef" for char in item)
                for item in self.seen_cursor_hashes
            )
            or len(set(self.seen_cursor_hashes)) != len(self.seen_cursor_hashes)
            or (
                self.last_error_code is not None and not isinstance(self.last_error_code, ErrorCode)
            )
            or (
                self.collection_complete
                and (not self.batch_ids or self.cursor is not None or self.pending is not None)
            )
            or (
                self.status == "completed"
                and (
                    not self.collection_complete
                    or self.dataset_id
                    != (None if self.request.granularity == "tick" else self.request.dataset_id)
                )
            )
            or (self.status != "completed" and self.dataset_id is not None)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid ingestion task state")


@dataclass(frozen=True)
class IngestionLease:
    task: IngestionTask
    token: UUID
    actor_id: UUID
    worker_id: UUID
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "expires_at", utc(self.expires_at))
        if (
            not isinstance(self.task, IngestionTask)
            or not isinstance(self.token, UUID)
            or not isinstance(self.actor_id, UUID)
            or not isinstance(self.worker_id, UUID)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid ingestion lease")


@dataclass(frozen=True)
class CollectionPolicy:
    min_interval_seconds: float = 1.0
    retry_base_seconds: float = 1.0
    retry_cap_seconds: float = 300.0
    max_failures: int = 5
    lease_seconds: float = 60.0

    def __post_init__(self) -> None:
        if (
            any(
                type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                for value in (
                    self.min_interval_seconds,
                    self.retry_base_seconds,
                    self.retry_cap_seconds,
                    self.lease_seconds,
                )
            )
            or self.retry_cap_seconds < self.retry_base_seconds
            or type(self.max_failures) is not int
            or not 1 <= self.max_failures <= 100
        ):
            raise ValueError("invalid collection policy")

    def delay(self, failures: int, retry_after: float | None) -> timedelta:
        if retry_after is not None and (
            type(retry_after) not in (int, float)
            or not math.isfinite(retry_after)
            or retry_after < 0
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid provider retry deadline")
        delay = min(self.retry_cap_seconds, self.retry_base_seconds * 2 ** min(failures, 100))
        return timedelta(seconds=max(delay, retry_after or 0))


def stage(task: IngestionTask, page: StagedPage) -> IngestionTask:
    if task.collection_complete or task.status != "running":
        raise PortError(ErrorCode.CONFLICT, "collection page cannot be staged")
    if len(page.page.records) > task.request.page_size:
        raise PortError(ErrorCode.INVALID_INPUT, "provider page exceeds bounds or repeats cursor")
    if task.pending is not None and task.pending != page:
        raise PortError(ErrorCode.CONFLICT, "staged collection page is immutable")
    return replace(task, pending=page)


def checkpoint(task: IngestionTask, batch_id: UUID, now: datetime) -> IngestionTask:
    if (
        task.status != "running"
        or task.pending is None
        or batch_id != task.request.batch_id(len(task.batch_ids))
    ):
        raise PortError(ErrorCode.CONFLICT, "collection checkpoint mismatch")
    next_cursor = task.pending.page.next_cursor
    cursor_hashes = task.seen_cursor_hashes
    if next_cursor is not None:
        cursor_hash = sha256(next_cursor.encode()).hexdigest()
        if cursor_hash not in cursor_hashes:
            cursor_hashes += (cursor_hash,)
    repeated = task.pending.continuation_repeated
    return replace(
        task,
        status="failed" if repeated else "queued",
        batch_ids=task.batch_ids + (batch_id,),
        cursor=next_cursor,
        collection_complete=next_cursor is None,
        pending=None,
        failures=0,
        last_error_code=ErrorCode.INVALID_INPUT if repeated else None,
        seen_cursor_hashes=cursor_hashes,
        next_attempt_at=utc(now),
    )


def schedule(
    task: IngestionTask,
    code: ErrorCode,
    deadline: datetime,
    *,
    terminal: bool,
    count_failure: bool = True,
) -> IngestionTask:
    return replace(
        task,
        status="failed" if terminal else "waiting",
        last_error_code=code,
        failures=task.failures + int(count_failure),
        next_attempt_at=utc(deadline),
    )


def finish(task: IngestionTask, dataset_id: UUID | None, now: datetime) -> IngestionTask:
    expected_dataset_id = None if task.request.granularity == "tick" else task.request.dataset_id
    if (
        task.status != "running"
        or not task.collection_complete
        or dataset_id != expected_dataset_id
    ):
        raise PortError(ErrorCode.CONFLICT, "collection result mismatch")
    return replace(
        task,
        status="completed",
        dataset_id=dataset_id,
        failures=0,
        last_error_code=None,
        next_attempt_at=utc(now),
    )


class HistoricalProviderPort(Protocol):
    @property
    def source(self) -> str: ...
    def fetch(self, request: CollectionRequest, cursor: str | None) -> ProviderPage: ...


class IngestionRepositoryPort(Protocol):
    def create_many(
        self,
        actor_id: UUID,
        requests: tuple[CollectionRequest, ...],
        *,
        avoid_overlap: bool = False,
    ) -> tuple[IngestionTask, ...]:
        """Atomically create scoped tasks; conflicting retries leave no partial group."""
        ...

    def create(
        self,
        actor_id: UUID,
        request: CollectionRequest,
        *,
        avoid_overlap: bool = False,
    ) -> IngestionTask: ...
    def get(self, workspace_id: UUID, task_id: UUID) -> IngestionTask: ...
    def list_tasks(self, workspace_id: UUID, limit: int = 100) -> tuple[IngestionTask, ...]: ...
    def overlapping_tasks(self, request: CollectionRequest) -> tuple[IngestionTask, ...]: ...
    def requeue(
        self, actor_id: UUID, workspace_id: UUID, task_id: UUID, now: datetime
    ) -> IngestionTask: ...
    def claim(
        self,
        actor_id: UUID,
        worker_id: UUID,
        workspace_id: UUID,
        task_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> IngestionLease | None: ...
    def stage(self, lease: IngestionLease, page: StagedPage, now: datetime) -> IngestionLease: ...
    def checkpoint(self, lease: IngestionLease, batch_id: UUID, now: datetime) -> IngestionTask: ...
    def schedule(
        self,
        lease: IngestionLease,
        code: ErrorCode,
        deadline: datetime,
        now: datetime,
        *,
        terminal: bool,
        count_failure: bool = True,
    ) -> IngestionTask: ...
    def finish(
        self, lease: IngestionLease, dataset_id: UUID | None, now: datetime
    ) -> IngestionTask: ...
    def reserve_rate_slot(
        self, request: CollectionRequest, now: datetime, interval: timedelta
    ) -> datetime: ...
    def defer_source(self, request: CollectionRequest, deadline: datetime) -> None: ...
