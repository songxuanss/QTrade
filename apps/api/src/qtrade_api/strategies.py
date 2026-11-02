"""Workspace-scoped Strategy and immutable version HTTP adapter."""

import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from qtrade_api.audit_context import request_context
from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.strategies import StrategyService
from qtrade_application.strategy_lifecycle import StrategyLifecycleService
from qtrade_application.strategy_validation import validate_strategy_source
from qtrade_contracts import StrategyVersion
from qtrade_domain.audit_port import AuditDetails, AuditRecord, AuditStorePort, contextualize
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import PortError
from qtrade_domain.strategies import (
    Strategy,
    StrategyStateTransition,
    StrategyVersionApproval,
    StrategyVersionState,
)


class StrategyVersionInput(BaseModel):
    code: str = Field(min_length=1, max_length=1_000_000)
    parameter_schema: dict[str, object]
    dependencies: dict[str, str] = Field(default_factory=dict, max_length=128)


class ValidateStrategyParameters(BaseModel):
    parameters: dict[str, object] = Field(default_factory=dict, max_length=128)


class ValidateStrategySource(BaseModel):
    code: str = Field(min_length=1, max_length=1_000_000)
    dependencies: dict[str, str] = Field(default_factory=dict, max_length=128)


class StrategyInput(StrategyVersionInput):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class LifecycleTransitionInput(BaseModel):
    state: StrategyVersionState
    reason: str = Field(default="", max_length=512)


class LifecycleApprovalInput(BaseModel):
    reason: str = Field(min_length=1, max_length=512)


def strategy_router(
    service: StrategyService | None,
    security: ApiSecurity,
    audit: AuditStorePort | None = None,
    lifecycle: StrategyLifecycleService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/strategies", tags=["strategies"])

    def configured() -> StrategyService:
        if service is None:
            raise HTTPException(
                503, "strategies unavailable", headers={"Cache-Control": "no-store"}
            )
        return service

    def lifecycle_configured() -> StrategyLifecycleService:
        if lifecycle is None:
            raise HTTPException(
                503, "strategy lifecycle unavailable", headers={"Cache-Control": "no-store"}
            )
        return lifecycle

    @router.get("")
    def list_strategies(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        include_versions: Annotated[bool, Query()] = False,
    ) -> list[dict[str, object]]:
        try:
            if include_versions:
                return [
                    {**_strategy(item), "strategy_versions": [_version(v) for v in versions]}
                    for item, versions in configured().list_with_versions(access, limit)
                ]
            return [_strategy(item) for item in configured().list(access, limit)]
        except PortError as error:
            raise public_error(error) from None

    @router.post("", status_code=201)
    def create(
        body: StrategyInput,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.STRATEGY_WRITE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            strategy, version = configured().create(
                access,
                name=body.name,
                description=body.description,
                code=body.code,
                parameter_schema_json=json.dumps(body.parameter_schema),
                dependencies_json=json.dumps(body.dependencies),
            )
            _audit(audit, access, request, "strategy.create", strategy.strategy_id)
            return {"strategy": _strategy(strategy), "version": _version(version)}
        except PortError as error:
            raise public_error(error) from None

    @router.post("/static-validation")
    def validate_source(
        body: ValidateStrategySource,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.STRATEGY_WRITE, mutate=True))
        ],
    ) -> dict[str, object]:
        access.require(Permission.STRATEGY_WRITE)
        result = validate_strategy_source(body.code, body.dependencies)
        return {
            "passed": result.passed,
            "ast_node_count": result.ast_node_count,
            "diagnostics": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "message": item.message,
                    "line": item.line,
                    "column": item.column,
                }
                for item in result.diagnostics
            ],
        }

    @router.get("/{strategy_id}")
    def detail(
        strategy_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            return _strategy(configured().get(access, strategy_id))
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{strategy_id}/versions")
    def versions(
        strategy_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        try:
            return [_version(item) for item in configured().versions(access, strategy_id)]
        except PortError as error:
            raise public_error(error) from None

    @router.post("/{strategy_id}/versions", status_code=201)
    def revise(
        strategy_id: UUID,
        body: StrategyVersionInput,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.STRATEGY_WRITE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            version = configured().revise(
                access,
                strategy_id,
                code=body.code,
                parameter_schema_json=json.dumps(body.parameter_schema),
                dependencies_json=json.dumps(body.dependencies),
            )
            _audit(audit, access, request, "strategy.version.create", version.strategy_version_id)
            return _version(version)
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{strategy_id}/versions/diff")
    def diff(
        strategy_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        before: Annotated[int, Query(ge=1)],
        after: Annotated[int, Query(ge=1)],
    ) -> dict[str, object]:
        try:
            result = configured().diff(access, strategy_id, before, after)
            return {
                "before": result.before.version,
                "after": result.after.version,
                "code_unified_diff": result.code_unified_diff,
                "parameter_schema_changed": result.parameter_schema_changed,
                "dependencies_changed": result.dependencies_changed,
            }
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{strategy_id}/versions/{version}/source")
    def source(
        strategy_id: UUID,
        version: int,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, str]:
        try:
            return {"code": configured().read_code(access, strategy_id, version)}
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{strategy_id}/versions/{version}/lifecycle")
    def get_lifecycle(
        strategy_id: UUID,
        version: int,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            service = lifecycle_configured()
            state = service.state(access, strategy_id, version)
            approval = service.approval(access, strategy_id, version)
            return {
                "state": state.state.value,
                "updated_at": state.updated_at.isoformat(),
                "approval": _approval(approval) if approval else None,
                "history": [
                    _transition(item) for item in service.history(access, strategy_id, version)
                ],
            }
        except PortError as error:
            raise public_error(error) from None

    @router.post("/{strategy_id}/versions/{version}/lifecycle/transitions")
    def transition_lifecycle(
        strategy_id: UUID,
        version: int,
        body: LifecycleTransitionInput,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.STRATEGY_WRITE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            state = lifecycle_configured().transition(
                access, strategy_id, version, body.state, reason=body.reason
            )
            _audit(
                audit, access, request, "strategy.lifecycle.transition", state.strategy_version_id
            )
            return {"state": state.state.value, "updated_at": state.updated_at.isoformat()}
        except PortError as error:
            raise public_error(error) from None

    @router.post("/{strategy_id}/versions/{version}/lifecycle/approve")
    def approve_lifecycle(
        strategy_id: UUID,
        version: int,
        body: LifecycleApprovalInput,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.LIVE_APPROVE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            state, approval = lifecycle_configured().approve(
                access, strategy_id, version, reason=body.reason
            )
            _audit(
                audit, access, request, "strategy.lifecycle.approve", approval.strategy_version_id
            )
            return {"state": state.state.value, "approval": _approval(approval)}
        except PortError as error:
            raise public_error(error) from None

    @router.post("/{strategy_id}/versions/{version}/lifecycle/launch")
    def launch_lifecycle(
        strategy_id: UUID,
        version: int,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.LIVE_START, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            state = lifecycle_configured().launch(access, strategy_id, version)
            _audit(audit, access, request, "strategy.lifecycle.launch", state.strategy_version_id)
            return {"state": state.state.value, "updated_at": state.updated_at.isoformat()}
        except PortError as error:
            raise public_error(error) from None

    @router.post("/{strategy_id}/archive")
    def archive_strategy(
        strategy_id: UUID,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.STRATEGY_ARCHIVE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            strategy = lifecycle_configured().archive_strategy(access, strategy_id)
            _audit(audit, access, request, "strategy.archive", strategy_id)
            return _strategy(strategy)
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{strategy_id}/versions/{version}/dependencies")
    def dependencies(
        strategy_id: UUID,
        version: int,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            return {
                "dependencies": json.loads(
                    configured().read_dependencies(access, strategy_id, version)
                )
            }
        except PortError as error:
            raise public_error(error) from None

    @router.post("/{strategy_id}/versions/{version}/parameters/validate")
    def validate_parameters(
        strategy_id: UUID,
        version: int,
        body: ValidateStrategyParameters,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.STRATEGY_WRITE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            return {
                "parameters": configured().validate_parameters(
                    access, strategy_id, version, body.parameters
                )
            }
        except PortError as error:
            raise public_error(error) from None

    return router


def _strategy(item: Strategy) -> dict[str, object]:
    return {
        "strategy_id": str(item.strategy_id),
        "workspace_id": str(item.workspace_id),
        "name": item.name,
        "description": item.description,
        "created_by": str(item.created_by),
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "current_version": item.current_version,
        "archived": item.archived,
    }


def _version(item: StrategyVersion) -> dict[str, object]:
    return {
        "strategy_id": str(item.strategy_id),
        "strategy_version_id": str(item.strategy_version_id),
        "workspace_id": str(item.workspace_id),
        "version": item.version,
        "created_by": str(item.created_by),
        "created_at": item.created_at.isoformat(),
        "code_sha256": item.code_sha256,
        "parameter_schema": json.loads(item.parameter_schema_json),
        "dependencies_sha256": item.dependencies_sha256,
    }


def _transition(item: StrategyStateTransition) -> dict[str, object]:
    return {
        "transition_id": str(item.transition_id),
        "from_state": item.from_state.value,
        "to_state": item.to_state.value,
        "actor_id": str(item.actor_id),
        "occurred_at": item.occurred_at.isoformat(),
        "reason": item.reason,
        "evidence": json.loads(item.evidence_json),
    }


def _approval(item: StrategyVersionApproval) -> dict[str, object]:
    return {
        "approval_id": str(item.approval_id),
        "strategy_version_id": str(item.strategy_version_id),
        "approver_id": str(item.approver_id),
        "approved_at": item.approved_at.isoformat(),
        "backtest_run_id": str(item.backtest_run_id),
        "paper_observations": item.paper_observations,
        "reason": item.reason,
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
