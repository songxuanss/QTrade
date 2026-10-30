"""Fenced, rate-limited historical acquisition with durable page checkpoints."""

from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID

from qtrade_application.batches import RawBatchService
from qtrade_application.datasets import DatasetReplayService
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.datasets import ReplayRequest
from qtrade_domain.history_sources import HistoricalSource
from qtrade_domain.ingestion import (
    CollectionPolicy,
    CollectionRequest,
    HistoricalProviderPort,
    IngestionLease,
    IngestionRepositoryPort,
    IngestionTask,
    ProviderPage,
    StagedPage,
    utc,
)
from qtrade_domain.ports import ErrorCode, PortError


class HistoricalIngestionWorker:
    def __init__(
        self,
        repository: IngestionRepositoryPort,
        batches: RawBatchService,
        datasets: DatasetReplayService,
        providers: tuple[HistoricalProviderPort, ...],
        policy: CollectionPolicy | None = None,
        specifications: tuple[HistoricalSource, ...] = (),
    ):
        self._repository, self._batches, self._datasets = repository, batches, datasets
        self._providers = {provider.source: provider for provider in providers}
        if len(self._providers) != len(providers):
            raise ValueError("duplicate historical provider source")
        self._policy = policy or CollectionPolicy()
        self.specifications = specifications
        if any(spec.source not in self._providers for spec in specifications):
            raise ValueError("specification source is not configured")

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def submit(
        self,
        access: WorkspaceAccess,
        request: CollectionRequest,
        *,
        avoid_overlap: bool = False,
    ) -> IngestionTask:
        access.require(Permission.DATA_MANAGE, object_workspace_id=request.workspace_id)
        provider = self._providers.get(request.source)
        if provider is None or provider.source != request.source:
            raise PortError(ErrorCode.UNSUPPORTED, "historical source is not configured")
        self.validate_request(request)
        return self._repository.create(access.user_id, request, avoid_overlap=avoid_overlap)

    def validate_request(self, request: CollectionRequest) -> None:
        for specification in self.specifications:
            if specification.source == request.source:
                specification.validate(request)
                return
        if self.specifications:
            raise PortError(ErrorCode.UNSUPPORTED, "source is not configured")

    def submit_many(
        self,
        access: WorkspaceAccess,
        requests: tuple[CollectionRequest, ...],
        *,
        avoid_overlap: bool = False,
    ) -> tuple[IngestionTask, ...]:
        for request in requests:
            access.require(Permission.DATA_MANAGE, object_workspace_id=request.workspace_id)
            if request.source not in self._providers:
                raise PortError(ErrorCode.UNSUPPORTED, "historical source is not configured")
            self.validate_request(request)
        return self._repository.create_many(access.user_id, requests, avoid_overlap=avoid_overlap)

    def rerun(self, access: WorkspaceAccess, task_id: UUID, now: datetime) -> IngestionTask:
        access.require(Permission.DATA_MANAGE)
        return self._repository.requeue(
            access.user_id, access.workspace.workspace_id, task_id, utc(now)
        )

    def _retry(
        self,
        lease: IngestionLease,
        code: ErrorCode,
        now: datetime,
        *,
        retry_after: float | None = None,
        count_failure: bool = True,
    ) -> IngestionTask:
        task = lease.task
        delay = self._policy.delay(task.failures, retry_after)
        deadline = now + delay
        terminal = code not in {
            ErrorCode.RATE_LIMITED,
            ErrorCode.UNAVAILABLE,
            ErrorCode.TIMEOUT,
        } or (count_failure and task.failures + 1 >= self._policy.max_failures)
        # Local slot contention must not extend the source-wide cooldown. With
        # many tasks that would keep moving the deadline and starve the source.
        # Provider Retry-After remains a shared cooldown across all tasks.
        if code == ErrorCode.RATE_LIMITED and count_failure:
            self._repository.defer_source(task.request, deadline)
        return self._repository.schedule(
            lease, code, deadline, now, terminal=terminal, count_failure=count_failure
        )

    def run_once(
        self, access: WorkspaceAccess, task_id: UUID, worker_id: UUID, now: datetime
    ) -> IngestionTask:
        now = utc(now)
        access.require(Permission.DATA_MANAGE)
        if not isinstance(worker_id, UUID):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid ingestion worker identity")
        task = self._repository.get(access.workspace.workspace_id, task_id)
        request = task.request
        provider = self._providers.get(request.source)
        if provider is None or provider.source != request.source:
            raise PortError(ErrorCode.UNSUPPORTED, "historical source is not configured")
        if request.workspace_id != access.workspace.workspace_id:
            raise PortError(ErrorCode.NOT_FOUND, "collection task unavailable")
        lease = self._repository.claim(
            access.user_id,
            worker_id,
            request.workspace_id,
            task_id,
            now,
            now + timedelta(seconds=self._policy.lease_seconds),
        )
        if lease is None:
            return self._repository.get(request.workspace_id, task_id)
        task = lease.task
        if task.collection_complete:
            if request.granularity == "tick":
                try:
                    return self._repository.finish(lease, None, now)
                except PortError as error:
                    return self._retry(
                        lease,
                        error.code,
                        now,
                        retry_after=error.retry_after,
                        count_failure=error.retryable,
                    )
                except Exception:
                    return self._retry(lease, ErrorCode.UNAVAILABLE, now)
            try:
                result = self._datasets.replay(
                    access,
                    ReplayRequest(
                        request.workspace_id,
                        request.dataset_id,
                        task.batch_ids,
                        request.transformation_version,
                        request.start,
                        request.end,
                        request.created_at,
                    ),
                )
                return self._repository.finish(lease, result.metadata.dataset_id, now)
            except PortError as error:
                return self._retry(
                    lease,
                    error.code,
                    now,
                    retry_after=error.retry_after,
                    count_failure=error.retryable,
                )
            except Exception:
                return self._retry(lease, ErrorCode.UNAVAILABLE, now)

        if task.pending is None:
            try:
                deadline = self._repository.reserve_rate_slot(
                    request, now, timedelta(seconds=self._policy.min_interval_seconds)
                )
                if deadline > now:
                    return self._retry(
                        lease,
                        ErrorCode.RATE_LIMITED,
                        now,
                        retry_after=(deadline - now).total_seconds(),
                        count_failure=False,
                    )
                page = provider.fetch(request, task.cursor)
                if not isinstance(page, ProviderPage):
                    raise PortError(ErrorCode.INVALID_INPUT, "provider returned invalid page")
                if len(page.records) > request.page_size:
                    raise PortError(
                        ErrorCode.INVALID_INPUT, "provider page exceeds requested limit"
                    )
                cursor_hash = (
                    sha256(page.next_cursor.encode()).hexdigest()
                    if page.next_cursor is not None
                    else None
                )
                repeated = cursor_hash is not None and (
                    cursor_hash in task.seen_cursor_hashes or page.next_cursor == task.cursor
                )
                lease = self._repository.stage(
                    lease, StagedPage(page, now, continuation_repeated=repeated), now
                )
                task = lease.task
            except PortError as error:
                return self._retry(lease, error.code, now, retry_after=error.retry_after)
            except Exception:
                return self._retry(lease, ErrorCode.UNAVAILABLE, now)

        assert task.pending is not None
        batch_id = request.batch_id(len(task.batch_ids))
        try:
            self._batches.publish(
                access,
                request.batch(
                    len(task.batch_ids), task.pending.collected_at, task.pending.page.records
                ),
            )
        except PortError as error:
            return self._retry(lease, error.code, now, retry_after=error.retry_after)
        except Exception:
            return self._retry(lease, ErrorCode.UNAVAILABLE, now)
        return self._repository.checkpoint(lease, batch_id, now)
