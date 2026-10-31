"""Workspace-scoped Backtest Run submission, progress and cancellation routes."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, Field

from qtrade_api.audit_context import request_context
from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.backtest_export import ExportArtifact
from qtrade_application.backtesting import (
    BacktestService,
    BacktestSubmission,
    WalkForwardSubmission,
)
from qtrade_domain.audit_port import AuditDetails, AuditRecord, AuditStorePort, contextualize
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.backtesting import BacktestRunRecord
from qtrade_domain.ports import PortError


class WalkForwardInput(BaseModel):
    strategy_id: UUID
    strategy_version: int = Field(ge=1)
    dataset_id: UUID
    instrument_id: str = Field(min_length=1, max_length=256)
    start: AwareDatetime
    end: AwareDatetime
    train_window_seconds: int = Field(gt=0, le=315_360_000)
    validation_window_seconds: int = Field(gt=0, le=31_536_000)
    step_seconds: int = Field(gt=0, le=31_536_000)
    initial_cash: Decimal = Field(gt=0, max_digits=24, decimal_places=8)
    currency: str = Field(min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    cost_model_version: str = Field(min_length=1, max_length=128)
    parameters: dict[str, object] = Field(default_factory=dict, max_length=128)
    random_seed: int = Field(default=0, ge=0, le=2**63 - 1)
    benchmark_id: str | None = Field(default=None, max_length=128)
    execution_model: dict[str, object] = Field(default_factory=dict, max_length=32)
    universe_mode: Literal["dataset", "membership"] = "dataset"


class BacktestInput(BaseModel):
    strategy_id: UUID
    strategy_version: int = Field(ge=1)
    dataset_id: UUID
    start: AwareDatetime
    end: AwareDatetime
    initial_cash: Decimal = Field(gt=0, max_digits=24, decimal_places=8)
    currency: str = Field(min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    cost_model_version: str = Field(min_length=1, max_length=128)
    parameters: dict[str, object] = Field(default_factory=dict, max_length=128)
    random_seed: int = Field(default=0, ge=0, le=2**63 - 1)
    benchmark_id: str | None = Field(default=None, max_length=128)
    execution_model: dict[str, object] = Field(default_factory=dict, max_length=32)
    universe_mode: Literal["dataset", "membership"] = "dataset"


def backtesting_router(
    service: BacktestService | None,
    security: ApiSecurity,
    audit: AuditStorePort | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/backtests", tags=["backtests"])

    def configured() -> BacktestService:
        if service is None:
            raise HTTPException(
                503, "backtesting unavailable", headers={"Cache-Control": "no-store"}
            )
        return service

    @router.post("", status_code=202)
    def create(
        body: BacktestInput,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.BACKTEST_RUN, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            record = configured().create(
                access,
                BacktestSubmission(
                    body.strategy_id,
                    body.strategy_version,
                    body.dataset_id,
                    body.start,
                    body.end,
                    body.initial_cash,
                    body.currency,
                    body.cost_model_version,
                    body.parameters,
                    body.random_seed,
                    body.benchmark_id,
                    execution_model=body.execution_model,
                    universe_mode=body.universe_mode,
                ),
            )
            _audit(audit, access, request, "backtest.create", record.run.run_id)
            return _record(record)
        except PortError as error:
            raise public_error(error) from None

    @router.get("")
    def list_runs(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, object]]:
        try:
            return [_record(item) for item in configured().list(access, limit)]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/compare")
    def compare(
        run_id: Annotated[list[UUID], Query(min_length=2, max_length=20)],
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            return configured().compare(access, tuple(run_id)).model_dump(mode="json")
        except PortError as error:
            raise public_error(error) from None

    @router.get("/walk-forward/plan")
    def walk_forward_plan(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        dataset_id: UUID,
        instrument_id: Annotated[str, Query(min_length=1, max_length=256)],
        start: AwareDatetime,
        end: AwareDatetime,
        train_window_seconds: Annotated[int, Query(gt=0, le=315_360_000)],
        validation_window_seconds: Annotated[int, Query(gt=0, le=31_536_000)],
        step_seconds: Annotated[int, Query(gt=0, le=31_536_000)],
    ) -> dict[str, object]:
        try:
            plan = configured().plan_walk_forward(
                access,
                dataset_id,
                instrument_id,
                start,
                end,
                timedelta(seconds=train_window_seconds),
                timedelta(seconds=validation_window_seconds),
                timedelta(seconds=step_seconds),
            )
            return plan.model_dump(mode="json")
        except PortError as error:
            raise public_error(error) from None

    @router.post("/walk-forward", status_code=202)
    def start_walk_forward(
        body: WalkForwardInput,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.BACKTEST_RUN, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            result = configured().start_walk_forward(
                access,
                WalkForwardSubmission(
                    body.strategy_id,
                    body.strategy_version,
                    body.dataset_id,
                    body.instrument_id,
                    body.start,
                    body.end,
                    timedelta(seconds=body.train_window_seconds),
                    timedelta(seconds=body.validation_window_seconds),
                    timedelta(seconds=body.step_seconds),
                    body.initial_cash,
                    body.currency,
                    body.cost_model_version,
                    body.parameters,
                    body.random_seed,
                    body.benchmark_id,
                    body.execution_model,
                    body.universe_mode,
                ),
            )
            _audit(audit, access, request, "backtest.walk_forward", result.walk_forward_id)
            return result.model_dump(mode="json")
        except PortError as error:
            raise public_error(error) from None

    @router.get("/walk-forward/{walk_forward_id}/export")
    def export_walk_forward(
        walk_forward_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        export_format: Annotated[str, Query(alias="format", pattern="^(csv|html)$")] = "csv",
    ) -> Response:
        try:
            artifact = configured().export_walk_forward(access, walk_forward_id, export_format)
        except PortError as error:
            raise public_error(error) from None
        return _download(artifact)

    @router.get("/walk-forward/{walk_forward_id}")
    def get_walk_forward(
        walk_forward_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            return configured().walk_forward(access, walk_forward_id).model_dump(mode="json")
        except PortError as error:
            raise public_error(error) from None

    @router.get("/datasets")
    def list_datasets(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, object]]:
        try:
            return [item.model_dump(mode="json") for item in configured().datasets(access, limit)]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{run_id}")
    def get(
        run_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            return _record(configured().get(access, run_id))
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{run_id}/history")
    def history(
        run_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        try:
            return [
                {
                    "event_id": str(item.event_id),
                    "status": item.status,
                    "progress": item.progress,
                    "occurred_at": item.occurred_at.isoformat(),
                    "error_code": item.error_code,
                    "revision": item.revision,
                }
                for item in configured().history(access, run_id)
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{run_id}/report")
    def report(
        run_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            return configured().report(access, run_id).model_dump(mode="json")
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{run_id}/export")
    def export_report(
        run_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        export_format: Annotated[str, Query(alias="format", pattern="^(csv|html)$")] = "csv",
    ) -> Response:
        try:
            artifact = configured().export_run(access, run_id, export_format)
        except PortError as error:
            raise public_error(error) from None
        return _download(artifact)

    @router.get("/{run_id}/manifest")
    def manifest(
        run_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            return configured().manifest(access, run_id).model_dump(mode="json")
        except PortError as error:
            raise public_error(error) from None

    @router.post("/{run_id}/cancel")
    def cancel(
        run_id: UUID,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.BACKTEST_RUN, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            record = configured().cancel(access, run_id)
            _audit(audit, access, request, "backtest.cancel", run_id)
            return _record(record)
        except PortError as error:
            raise public_error(error) from None

    return router


def _download(artifact: ExportArtifact) -> Response:
    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _record(record: BacktestRunRecord) -> dict[str, object]:
    return {
        **record.run.model_dump(mode="json"),
        "engine_job_id": str(record.engine_job_id) if record.engine_job_id else None,
        "revision": record.revision,
    }


def _audit(
    store: AuditStorePort | None,
    access: WorkspaceAccess,
    request: Request,
    action: str,
    target_id: UUID,
) -> None:
    if store is None:
        return
    store.append(
        contextualize(
            AuditRecord(
                access.workspace.workspace_id,
                "user",
                access.user_id,
                action,
                "success",
                datetime.now(UTC),
                "api",
                uuid4(),
                details=AuditDetails(target_id=target_id),
            ),
            request_context(request, action),
        )
    )
