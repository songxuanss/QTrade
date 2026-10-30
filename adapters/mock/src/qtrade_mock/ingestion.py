"""Concurrent ingestion repository double; production lease rules mirror its contract."""

from collections.abc import Callable
from datetime import datetime, timedelta
from threading import RLock
from uuid import UUID, uuid4

from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.collection_coverage import matches
from qtrade_domain.ingestion import (
    CollectionRequest,
    IngestionLease,
    IngestionTask,
    StagedPage,
    utc,
)
from qtrade_domain.ingestion import (
    checkpoint as checkpoint_task,
)
from qtrade_domain.ingestion import (
    finish as finish_task,
)
from qtrade_domain.ingestion import (
    schedule as schedule_task,
)
from qtrade_domain.ingestion import (
    stage as stage_task,
)
from qtrade_domain.ports import ErrorCode, PortError


class MockIngestionRepository:
    def __init__(self, access_for_actor: Callable[[UUID, UUID], WorkspaceAccess | None]) -> None:
        self._access_for_actor = access_for_actor
        self._tasks: dict[tuple[UUID, UUID], IngestionTask] = {}
        self._leases: dict[tuple[UUID, UUID], tuple[UUID, datetime]] = {}
        self._rate: dict[tuple[UUID, str, str], datetime] = {}
        self._lock = RLock()

    def _access(self, actor_id: UUID, workspace_id: UUID) -> WorkspaceAccess:
        access = self._access_for_actor(actor_id, workspace_id)
        if not isinstance(access, WorkspaceAccess) or access.workspace.workspace_id != workspace_id:
            raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
        access.require(Permission.DATA_MANAGE, object_workspace_id=workspace_id)
        return access

    def create(
        self,
        actor_id: UUID,
        request: CollectionRequest,
        *,
        avoid_overlap: bool = False,
    ) -> IngestionTask:
        self._access(actor_id, request.workspace_id)
        task = IngestionTask(request, actor_id, "queued", request.created_at)
        key = (request.workspace_id, request.task_id)
        with self._lock:
            existing = self._tasks.get(key)
            if existing is not None:
                if existing.request != request or existing.actor_id != actor_id:
                    raise PortError(ErrorCode.CONFLICT, "collection task identity already used")
                return existing
            if avoid_overlap and any(
                item.status != "failed" and matches(request, item) for item in self._tasks.values()
            ):
                raise PortError(ErrorCode.CONFLICT, "collection coverage changed; recheck")
            self._tasks[key] = task
            return task

    def create_many(
        self,
        actor_id: UUID,
        requests: tuple[CollectionRequest, ...],
        *,
        avoid_overlap: bool = False,
    ) -> tuple[IngestionTask, ...]:
        if (
            not requests
            or len({item.workspace_id for item in requests}) != 1
            or len({item.task_id for item in requests}) != len(requests)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid collection task group")
        with self._lock:
            previous = dict(self._tasks)
            try:
                return tuple(
                    self.create(actor_id, item, avoid_overlap=avoid_overlap) for item in requests
                )
            except Exception:
                self._tasks = previous
                raise

    def get(self, workspace_id: UUID, task_id: UUID) -> IngestionTask:
        with self._lock:
            task = self._tasks.get((workspace_id, task_id))
        if task is None:
            raise PortError(ErrorCode.NOT_FOUND, "collection task unavailable")
        return task

    def list_tasks(self, workspace_id: UUID, limit: int = 100) -> tuple[IngestionTask, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid collection task page size")
        with self._lock:
            found = sorted(
                (task for (scope, _), task in self._tasks.items() if scope == workspace_id),
                key=lambda task: (task.next_attempt_at, str(task.request.task_id)),
                reverse=True,
            )
            return tuple(found[:limit])

    def overlapping_tasks(self, request: CollectionRequest) -> tuple[IngestionTask, ...]:
        with self._lock:
            return tuple(task for task in self._tasks.values() if matches(request, task))

    def requeue(
        self, actor_id: UUID, workspace_id: UUID, task_id: UUID, now: datetime
    ) -> IngestionTask:
        self._access(actor_id, workspace_id)
        now = utc(now)
        key = (workspace_id, task_id)
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                raise PortError(ErrorCode.NOT_FOUND, "collection task unavailable")
            if task.status == "completed":
                return task
            if task.status != "failed":
                raise PortError(ErrorCode.CONFLICT, "only failed collection tasks can be rerun")
            retry = task.__class__(
                task.request,
                task.actor_id,
                "queued",
                now,
                task.cursor,
                task.batch_ids,
                task.pending,
                task.collection_complete,
                seen_cursor_hashes=task.seen_cursor_hashes,
            )
            self._tasks[key] = retry
            return retry

    def claim(
        self,
        actor_id: UUID,
        worker_id: UUID,
        workspace_id: UUID,
        task_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> IngestionLease | None:
        now, expires_at = utc(now), utc(expires_at)
        self._access(actor_id, workspace_id)
        key = (workspace_id, task_id)
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                raise PortError(ErrorCode.NOT_FOUND, "collection task unavailable")
            if task.status not in {"queued", "waiting", "running"} or task.next_attempt_at > now:
                return None
            current = self._leases.get(key)
            if task.status == "running" and current is not None and current[1] > now:
                return None
            token = uuid4()
            self._tasks[key] = task.__class__(
                task.request,
                task.actor_id,
                "running",
                task.next_attempt_at,
                task.cursor,
                task.batch_ids,
                task.pending,
                task.collection_complete,
                task.failures,
                task.last_error_code,
                seen_cursor_hashes=task.seen_cursor_hashes,
            )
            self._leases[key] = (token, expires_at)
            return IngestionLease(self._tasks[key], token, actor_id, worker_id, expires_at)

    def _verify(
        self, lease: IngestionLease, now: datetime
    ) -> tuple[tuple[UUID, UUID], IngestionTask]:
        now = utc(now)
        key = (lease.task.request.workspace_id, lease.task.request.task_id)
        self._access(lease.actor_id, lease.task.request.workspace_id)
        task = self._tasks.get(key)
        current = self._leases.get(key)
        if (
            task is None
            or task.status != "running"
            or current != (lease.token, lease.expires_at)
            or lease.expires_at <= now
        ):
            raise PortError(ErrorCode.CONFLICT, "collection lease expired")
        return key, task

    def _lease(
        self, task: IngestionTask, token: UUID, actor_id: UUID, worker_id: UUID, expires: datetime
    ) -> IngestionLease:
        return IngestionLease(task, token, actor_id, worker_id, expires)

    def stage(self, lease: IngestionLease, page: StagedPage, now: datetime) -> IngestionLease:
        with self._lock:
            key, task = self._verify(lease, now)
            result = stage_task(task, page)
            self._tasks[key] = result
            return self._lease(
                result, lease.token, lease.actor_id, lease.worker_id, lease.expires_at
            )

    def checkpoint(self, lease: IngestionLease, batch_id: UUID, now: datetime) -> IngestionTask:
        with self._lock:
            key, task = self._verify(lease, now)
            result = checkpoint_task(task, batch_id, now)
            self._tasks[key] = result
            self._leases.pop(key, None)
            return result

    def schedule(
        self,
        lease: IngestionLease,
        code: ErrorCode,
        deadline: datetime,
        now: datetime,
        *,
        terminal: bool,
        count_failure: bool = True,
    ) -> IngestionTask:
        with self._lock:
            key, task = self._verify(lease, now)
            result = schedule_task(
                task, code, deadline, terminal=terminal, count_failure=count_failure
            )
            self._tasks[key] = result
            self._leases.pop(key, None)
            return result

    def finish(
        self, lease: IngestionLease, dataset_id: UUID | None, now: datetime
    ) -> IngestionTask:
        with self._lock:
            key, task = self._verify(lease, now)
            result = finish_task(task, dataset_id, now)
            self._tasks[key] = result
            self._leases.pop(key, None)
            return result

    def reserve_rate_slot(
        self, request: CollectionRequest, now: datetime, interval: timedelta
    ) -> datetime:
        now = utc(now)
        key = (request.workspace_id, request.source, request.authorization_id)
        with self._lock:
            deadline = self._rate.get(key, now)
            if deadline <= now:
                self._rate[key] = now + interval
                return now
            return deadline

    def defer_source(self, request: CollectionRequest, deadline: datetime) -> None:
        key = (request.workspace_id, request.source, request.authorization_id)
        deadline = utc(deadline)
        with self._lock:
            self._rate[key] = max(self._rate.get(key, deadline), deadline)
