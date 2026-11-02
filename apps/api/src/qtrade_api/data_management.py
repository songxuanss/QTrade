"""Workspace-scoped API for ingestion tasks, raw batches, and quality issues."""

import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from qtrade_api.audit_context import request_context
from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.data_management import DataManagementService
from qtrade_application.dataset_composition import (
    CompositionRequest,
    DatasetCompositionService,
)
from qtrade_application.datasets import DatasetReplayService
from qtrade_application.research_data import ResearchDataService
from qtrade_domain.audit_port import AuditDetails, AuditRecord, AuditStorePort, contextualize
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.datasets import DatasetRepositoryPort
from qtrade_domain.ingestion import CollectionRequest, IngestionTask
from qtrade_domain.ports import PortError
from qtrade_domain.quality import QualityIssue


class CollectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=256)
    authorization_id: str = Field(min_length=1, max_length=256)
    instrument_id: str = Field(min_length=1, max_length=256)
    start: datetime
    end: datetime
    granularity: Literal["tick", "1m", "5m", "15m", "1h", "1d"] = "tick"
    mode: Literal["check", "all", "missing"] = "check"


class ComposeDatasetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: UUID
    start: datetime
    end: datetime
    selection_size: int = Field(default=20, ge=1, le=50)
    granularity: Literal["1d"] = "1d"
    transformation_version: str = Field(
        default="mairui.unadjusted-bars.v1", min_length=1, max_length=256
    )


def data_management_router(
    service: DataManagementService | None,
    security: ApiSecurity,
    audit: AuditStorePort | None = None,
    research: ResearchDataService | None = None,
    datasets: DatasetRepositoryPort | None = None,
    replay: DatasetReplayService | None = None,
    composition: DatasetCompositionService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/data", tags=["data-management"])

    def configured() -> DataManagementService:
        if service is None:
            raise HTTPException(
                503, "data management unavailable", headers={"Cache-Control": "no-store"}
            )
        return service

    @router.get("/datasets")
    def research_datasets(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        if datasets is None:
            raise HTTPException(503, "research datasets unavailable")
        try:
            return [
                item.metadata.model_dump(mode="json")
                for item in datasets.list(access.workspace.workspace_id)
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/datasets/{dataset_id}/quality")
    def dataset_quality(
        dataset_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        if replay is None:
            raise HTTPException(503, "quality report unavailable")
        try:
            return replay.quality_report(access, dataset_id)
        except PortError as error:
            raise public_error(error) from None

    @router.post("/datasets/compose", status_code=201)
    def compose_dataset(
        body: ComposeDatasetBody,
        access: Annotated[
            WorkspaceAccess,
            Depends(security.workspace(Permission.DATA_MANAGE, mutate=True)),
        ],
    ) -> dict[str, object]:
        if composition is None:
            raise HTTPException(503, "dataset composition unavailable")
        try:
            result = composition.compose(
                access,
                CompositionRequest(
                    access.workspace.workspace_id,
                    body.snapshot_id,
                    body.start,
                    body.end,
                    selection_size=body.selection_size,
                    granularity=body.granularity,
                    transformation_version=body.transformation_version,
                ),
            )
        except PortError as error:
            raise public_error(error) from None
        return {
            "dataset": result.dataset.metadata.model_dump(mode="json"),
            "snapshot_id": str(result.snapshot_id),
            "selected": [
                {
                    "instrument_id": item.instrument_id,
                    "average_value": item.average_value,
                    "weeks": item.weeks,
                }
                for item in result.selected
            ],
            "excluded": list(result.excluded),
        }

    @router.post("/tasks/{task_id}/dataset", status_code=201)
    def publish_research(
        task_id: UUID,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
        output: Annotated[str, Query(pattern="^(1m|tick)$")] = "1m",
    ) -> dict[str, object]:
        if research is None:
            raise HTTPException(503, "research publication unavailable")
        try:
            dataset = research.publish(access, task_id, output)
            _audit(
                audit,
                access,
                request,
                "data.research.publish",
                dataset.metadata.dataset_id,
                "success",
            )
            return dataset.metadata.model_dump(mode="json")
        except PortError as error:
            raise public_error(error) from None

    @router.get("/tasks")
    def tasks(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, object]]:
        try:
            return [_task(item) for item in configured().list_tasks(access, limit)]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/download-policy")
    def download_policy(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        return {"max_days": configured().download_policy.max_days}

    @router.get("/source-options")
    def source_options(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        return configured().source_options(access)

    @router.get("/sources")
    def sources(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[str]:
        try:
            return list(configured().collection_sources(access))
        except PortError as error:
            raise public_error(error) from None

    @router.get("/batches")
    def batches(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, object]]:
        try:
            return [
                {
                    "batch_id": str(item.batch_id),
                    "source": item.source,
                    "ingestion_task_id": str(item.ingestion_task_id),
                    "instrument_id": item.instrument_id,
                    "granularity": item.granularity,
                    "collected_at": item.collected_at.isoformat(),
                    "content_sha256": item.content_sha256,
                    "record_count": item.record_count,
                }
                for item in configured().list_batches(access, limit)
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.post("/tasks", status_code=202)
    def create_task(
        body: CollectionBody,
        request: Request,
        access: Annotated[
            WorkspaceAccess,
            Depends(security.workspace(Permission.DATA_MANAGE, mutate=True)),
        ],
    ) -> dict[str, object]:
        start, end = body.start, body.end
        if start.utcoffset() is None or end.utcoffset() is None or start >= end:
            raise HTTPException(422, "download requires an aware, increasing range")
        policy = configured().download_policy
        if body.granularity == "tick" and policy.exceeded(start, end):
            raise HTTPException(
                422, {"code": "download_range_exceeded", "max_days": policy.max_days}
            )
        now = datetime.now(UTC)
        collection = CollectionRequest(
            access.workspace.workspace_id,
            uuid4(),
            body.source,
            body.authorization_id,
            body.instrument_id,
            body.granularity,
            start,
            end,
            now,
            "raw-tick.v1" if body.granularity == "tick" else "mairui.unadjusted-bars.v1",
        )
        try:
            plan = configured().collection_plan(access, collection)
            if body.mode == "check" and plan.overlapping:
                raise HTTPException(
                    409,
                    {
                        "code": "collection_choice_required",
                        "status": plan.status,
                        "missing": [
                            {"start": start.isoformat(), "end": end.isoformat()}
                            for start, end in plan.missing
                        ],
                        "reusable_tasks": len(plan.reuse),
                    },
                )
            if body.mode == "missing":
                results = configured().submit_missing(access, collection, plan)
                if not results:
                    return {"status": "already_downloaded", "submitted_task_ids": []}
                result = results[0]
            else:
                results = configured().submit_tasks(
                    access, collection, avoid_overlap=body.mode == "check"
                )
                result = results[0]
            for item in results:
                _audit(
                    audit,
                    access,
                    request,
                    "data.collection.submit"
                    if body.mode != "missing"
                    else "data.collection.resume",
                    item.request.task_id,
                    "success",
                )
            return {
                **_task(result),
                "submitted_task_ids": [str(task.request.task_id) for task in results],
            }
        except PortError as error:
            raise public_error(error) from None

    @router.get("/quality-issues")
    def issues(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, object]]:
        try:
            return [_issue(item) for item in configured().list_issues(access, limit)]
        except PortError as error:
            raise public_error(error) from None

    @router.post("/tasks/{task_id}/rerun")
    def rerun(
        task_id: UUID,
        request: Request,
        access: Annotated[
            WorkspaceAccess,
            Depends(security.workspace(Permission.DATA_MANAGE, mutate=True)),
        ],
    ) -> dict[str, object]:
        try:
            result = configured().rerun_task(access, task_id, datetime.now(UTC))
            _audit(audit, access, request, "data.collection.rerun", task_id, "success")
            return _task(result)
        except PortError as error:
            raise public_error(error) from None

    @router.post("/quality-issues/{issue_id}/claim")
    def claim_issue(
        issue_id: UUID,
        request: Request,
        access: Annotated[
            WorkspaceAccess,
            Depends(security.workspace(Permission.DATA_MANAGE, mutate=True)),
        ],
    ) -> dict[str, object]:
        try:
            result = configured().claim_issue(access, issue_id, datetime.now(UTC))
            _audit(audit, access, request, "data.quality.claim", issue_id, "success")
            return _issue(result)
        except PortError as error:
            raise public_error(error) from None

    @router.post("/quality-issues/{issue_id}/close")
    def close_issue(
        issue_id: UUID,
        request: Request,
        access: Annotated[
            WorkspaceAccess,
            Depends(security.workspace(Permission.DATA_MANAGE, mutate=True)),
        ],
    ) -> dict[str, object]:
        try:
            result = configured().close_issue(access, issue_id, datetime.now(UTC))
            _audit(audit, access, request, "data.quality.close", issue_id, "success")
            return _issue(result)
        except PortError as error:
            raise public_error(error) from None

    return router


def _task(task: IngestionTask) -> dict[str, object]:
    request = task.request
    return {
        "task_id": str(request.task_id),
        "source": request.source,
        "instrument_id": request.instrument_id,
        "granularity": request.granularity,
        "start": request.start.isoformat(),
        "end": request.end.isoformat(),
        "status": task.status,
        "next_attempt_at": task.next_attempt_at.isoformat(),
        "page_count": len(task.batch_ids),
        "collection_complete": task.collection_complete,
        "failures": task.failures,
        "last_error_code": task.last_error_code.value if task.last_error_code else None,
        "dataset_id": str(task.dataset_id) if task.dataset_id else None,
        "download_group_id": str(request.download_group_id) if request.download_group_id else None,
        "part_index": request.part_index,
        "part_count": request.part_count,
    }


def _issue(issue: QualityIssue) -> dict[str, object]:
    return {
        "issue_id": str(issue.issue_id),
        "dataset_id": str(issue.dataset_id),
        "code": issue.code,
        "severity": issue.severity,
        "instrument_id": issue.instrument_id,
        "granularity": issue.granularity,
        "starts_at": issue.starts_at.isoformat(),
        "ends_at": issue.ends_at.isoformat(),
        "batch_ids": [str(item) for item in issue.batch_ids],
        "evidence": json.loads(issue.evidence),
        "detected_at": issue.detected_at.isoformat(),
        "status": issue.status,
        "claimed_by": str(issue.claimed_by) if issue.claimed_by else None,
        "closed_by": str(issue.closed_by) if issue.closed_by else None,
        "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
    }


def _audit(
    store: AuditStorePort | None,
    access: WorkspaceAccess,
    request: Request,
    action: str,
    target_id: UUID,
    result: str,
) -> None:
    if store is not None:
        store.append(
            contextualize(
                AuditRecord(
                    access.workspace.workspace_id,
                    "user",
                    access.user_id,
                    action,
                    result,
                    datetime.now(UTC),
                    "api",
                    uuid4(),
                    details=AuditDetails(target_id=target_id),
                ),
                request_context(request, action),
            )
        )
