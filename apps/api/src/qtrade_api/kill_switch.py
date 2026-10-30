"""Workspace-scoped Kill Switch APIs with CSRF, role and audit enforcement."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.kill_switch import KillSwitchService
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import PortError


class ActivateInput(BaseModel):
    account_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=500)


class RecoveryInput(BaseModel):
    account_id: UUID | None = None


class ApproveRecoveryInput(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def kill_switch_router(service: KillSwitchService | None, security: ApiSecurity) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/kill-switch", tags=["risk"])

    @router.get("")
    def state(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        account_id: UUID | None = None,
    ) -> dict[str, object]:
        if service is None:
            raise HTTPException(503, "kill switch unavailable")
        try:
            current = service.state(access, account_id)
            return {
                "active": current.active if current else False,
                "reason": current.reason if current else None,
                "revision": current.revision if current else 0,
                "changed_at": current.changed_at.isoformat() if current else None,
                "account_id": str(current.account_id) if current and current.account_id else None,
            }
        except PortError as error:
            raise public_error(error) from None

    @router.get("/recovery")
    def pending_recoveries(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        if service is None:
            raise HTTPException(503, "kill switch unavailable")
        try:
            return [
                {
                    "request_id": str(item.request_id),
                    "self_requested": item.requested_by == access.user_id,
                    "account_id": str(item.account_id) if item.account_id else None,
                    "requested_at": item.requested_at.isoformat(),
                }
                for item in service.pending_recoveries(access)
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.post("/activate")
    def activate(
        body: ActivateInput,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.KILL_SWITCH, mutate=True))
        ],
    ) -> dict[str, object]:
        if service is None:
            raise HTTPException(503, "kill switch unavailable")
        try:
            result = service.activate(
                access,
                account_id=body.account_id,
                reason=body.reason,
                now=datetime.now(UTC),
            )
            return {"active": result.active, "revision": result.revision}
        except PortError as error:
            raise public_error(error) from None

    @router.post("/recovery")
    def request_recovery(
        body: RecoveryInput,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.KILL_SWITCH, mutate=True))
        ],
    ) -> dict[str, object]:
        if service is None:
            raise HTTPException(503, "kill switch unavailable")
        try:
            result = service.request_recovery(
                access, account_id=body.account_id, now=datetime.now(UTC)
            )
            return {"request_id": str(result.request_id), "status": "pending"}
        except PortError as error:
            raise public_error(error) from None

    @router.post("/recovery/{request_id}/approve")
    def approve_recovery(
        request_id: UUID,
        body: ApproveRecoveryInput,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.KILL_SWITCH, mutate=True))
        ],
    ) -> dict[str, object]:
        if service is None:
            raise HTTPException(503, "kill switch unavailable")
        try:
            result = service.approve_recovery(
                access,
                request_id,
                reason=body.reason,
                now=datetime.now(UTC),
            )
            return {"active": result.active, "revision": result.revision}
        except PortError as error:
            raise public_error(error) from None

    return router
