"""Read and operate on a workspace's ingestion tasks, batches, and quality issues."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

from qtrade_application.ingestion import HistoricalIngestionWorker
from qtrade_contracts import DataBatch
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.batches import BatchRepositoryPort
from qtrade_domain.collection_coverage import CollectionPlan, plan_collection
from qtrade_domain.collection_parts import split_collection
from qtrade_domain.download_policy import DownloadRangePolicy
from qtrade_domain.ingestion import CollectionRequest, IngestionRepositoryPort, IngestionTask
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.quality import QualityIssue, QualityIssueStorePort


class DataManagementService:
    def __init__(
        self,
        tasks: IngestionRepositoryPort,
        batches: BatchRepositoryPort,
        issues: QualityIssueStorePort,
        ingestion: HistoricalIngestionWorker | None = None,
        download_policy: DownloadRangePolicy | None = None,
    ):
        self._tasks, self._batches, self._issues = tasks, batches, issues
        self._ingestion = ingestion
        self.download_policy = download_policy or DownloadRangePolicy()

    def submit_task(
        self,
        access: WorkspaceAccess,
        request: CollectionRequest,
        *,
        avoid_overlap: bool = False,
    ) -> IngestionTask:
        access.require(Permission.DATA_MANAGE, object_workspace_id=request.workspace_id)
        if request.granularity == "tick":
            self.download_policy.validate(request.start, request.end)
        if self._ingestion is None:
            raise PortError(ErrorCode.UNAVAILABLE, "historical collection is not configured")
        return self._ingestion.submit(access, request, avoid_overlap=avoid_overlap)

    def collection_sources(self, access: WorkspaceAccess) -> tuple[str, ...]:
        access.require(Permission.READ)
        return self._ingestion.sources if self._ingestion is not None else ()

    def submit_tasks(
        self, access: WorkspaceAccess, request: CollectionRequest, *, avoid_overlap: bool = False
    ) -> tuple[IngestionTask, ...]:
        access.require(Permission.DATA_MANAGE, object_workspace_id=request.workspace_id)
        if request.granularity == "tick":
            self.download_policy.validate(request.start, request.end)
        if self._ingestion is None:
            raise PortError(ErrorCode.UNAVAILABLE, "historical collection is not configured")
        self._ingestion.validate_request(request)
        return self._ingestion.submit_many(
            access, split_collection(request), avoid_overlap=avoid_overlap
        )

    def source_options(self, access: WorkspaceAccess) -> list[dict[str, object]]:
        access.require(Permission.READ)
        return (
            [
                {
                    "source": item.source,
                    "authorization_id": item.authorization_id,
                    "granularities": item.granularities,
                    "earliest": item.earliest.isoformat() if item.earliest else None,
                }
                for item in self._ingestion.specifications
            ]
            if self._ingestion
            else []
        )

    def collection_plan(
        self, access: WorkspaceAccess, request: CollectionRequest
    ) -> CollectionPlan:
        access.require(Permission.DATA_MANAGE, object_workspace_id=request.workspace_id)
        if request.granularity == "tick":
            self.download_policy.validate(request.start, request.end)
        if self._ingestion is not None:
            self._ingestion.validate_request(request)
        return plan_collection(request, self._tasks.overlapping_tasks(request))

    def submit_missing(
        self,
        access: WorkspaceAccess,
        request: CollectionRequest,
        plan: CollectionPlan,
    ) -> tuple[IngestionTask, ...]:
        access.require(Permission.DATA_MANAGE, object_workspace_id=request.workspace_id)
        if request.granularity == "tick":
            self.download_policy.validate(request.start, request.end)
        tasks = []
        for task in plan.reuse:
            tasks.append(
                self.rerun_task(access, task.request.task_id, request.created_at)
                if task.status == "failed"
                else task
            )
        for start, end in plan.submit_ranges:
            tasks.extend(
                self.submit_tasks(
                    access,
                    replace(request, task_id=uuid4(), start=start, end=end),
                    avoid_overlap=True,
                )
            )
        return tuple(tasks)

    def list_tasks(self, access: WorkspaceAccess, limit: int = 100) -> tuple[IngestionTask, ...]:
        access.require(Permission.READ)
        result = self._tasks.list_tasks(access.workspace.workspace_id, limit)
        if len(result) > limit or any(
            task.request.workspace_id != access.workspace.workspace_id for task in result
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "task repository returned invalid scope")
        return result

    def list_batches(self, access: WorkspaceAccess, limit: int = 100) -> tuple[DataBatch, ...]:
        access.require(Permission.READ)
        result = self._batches.list(access.workspace.workspace_id, limit)
        if len(result) > limit or any(
            not isinstance(batch, DataBatch) or batch.workspace_id != access.workspace.workspace_id
            for batch in result
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "batch repository returned invalid scope")
        return result

    def list_issues(self, access: WorkspaceAccess, limit: int = 100) -> tuple[QualityIssue, ...]:
        access.require(Permission.READ)
        result = self._issues.list_issues(access.workspace.workspace_id, limit=limit)
        if len(result) > limit or any(
            issue.workspace_id != access.workspace.workspace_id for issue in result
        ):
            raise PortError(
                ErrorCode.UNAVAILABLE, "quality issue repository returned invalid scope"
            )
        return result

    def rerun_task(self, access: WorkspaceAccess, task_id: UUID, now: datetime) -> IngestionTask:
        access.require(Permission.DATA_MANAGE)
        return self._tasks.requeue(access.user_id, access.workspace.workspace_id, task_id, now)

    def claim_issue(self, access: WorkspaceAccess, issue_id: UUID, now: datetime) -> QualityIssue:
        access.require(Permission.DATA_MANAGE)
        return self._issues.claim_issue(
            access.user_id, access.workspace.workspace_id, issue_id, now
        )

    def close_issue(self, access: WorkspaceAccess, issue_id: UUID, now: datetime) -> QualityIssue:
        access.require(Permission.DATA_MANAGE)
        return self._issues.close_issue(
            access.user_id, access.workspace.workspace_id, issue_id, now
        )
