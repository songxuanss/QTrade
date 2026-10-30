"""CSRF- and role-protected execution runtime controls."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from qtrade_api.audit_context import request_context
from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.runtime_control import RuntimeControlService
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import PortError


class RuntimeControlInput(BaseModel):
    action: Literal["pause", "resume", "stop", "cancel_orders", "close_positions"]
    confirmation: str | None = Field(default=None, max_length=64)


def runtime_control_router(
    service: RuntimeControlService | None, security: ApiSecurity
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/runtimes", tags=["execution"])

    @router.get("")
    def list_runtimes(
        access: Annotated[
            WorkspaceAccess,
            Depends(security.workspace(Permission.READ)),
        ],
    ) -> list[dict[str, object]]:
        if service is None:
            raise HTTPException(503, "runtime list unavailable")
        try:
            return [
                {
                    "runtime_id": str(runtime.runtime_id),
                    "workspace_id": str(runtime.workspace_id),
                    "account_id": str(runtime.account_id),
                    "strategy_version_id": str(runtime.strategy_version_id),
                    "mode": runtime.mode,
                    "state": runtime.state,
                    "revision": runtime.revision,
                    "updated_at": runtime.updated_at.isoformat(),
                }
                for runtime in service.list(access)
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.post("/{runtime_id}/control")
    def control(
        runtime_id: UUID,
        body: RuntimeControlInput,
        request: Request,
        access: Annotated[
            WorkspaceAccess,
            Depends(security.workspace(Permission.RUNTIME_CONTROL, mutate=True)),
        ],
    ) -> dict[str, object]:
        if service is None:
            raise HTTPException(503, "runtime controls unavailable")
        try:
            result = service.control(
                access,
                runtime_id,
                body.action,
                confirmation=body.confirmation,
                now=datetime.now(UTC),
                audit_context=request_context(request, "api.runtime.control"),
            )
            return {
                "action": result.action,
                "affected_count": result.affected_count,
                "runtime": {
                    "runtime_id": str(result.runtime.runtime_id),
                    "workspace_id": str(result.runtime.workspace_id),
                    "account_id": str(result.runtime.account_id),
                    "strategy_version_id": str(result.runtime.strategy_version_id),
                    "mode": result.runtime.mode,
                    "state": result.runtime.state,
                    "revision": result.runtime.revision,
                    "updated_at": result.runtime.updated_at.isoformat(),
                },
            }
        except PortError as error:
            raise public_error(error) from None

    return router
