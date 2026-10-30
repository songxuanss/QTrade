"""Workspace resources expose safe identity metadata, never provider credentials."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from qtrade_api.audit_context import request_context
from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.workspaces import WorkspaceService
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.identity import Membership, Role, Session
from qtrade_domain.ports import PortError


class MemberChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role
    active: StrictBool


def member_view(member: Membership) -> dict[str, object]:
    return {
        "workspace_id": str(member.workspace_id),
        "user_id": str(member.user_id),
        "role": member.role.value,
        "active": member.active,
        "created_at": member.created_at.isoformat(),
        "updated_at": member.updated_at.isoformat(),
    }


def workspace_view(access: WorkspaceAccess) -> dict[str, object]:
    workspace = access.workspace
    return {
        "workspace_id": str(workspace.workspace_id),
        "name": workspace.name,
        "kind": workspace.kind,
        "live_trading_enabled": workspace.live_trading_enabled,
        "role": access.role.value,
        "permissions": sorted(access.permissions),
    }


def workspace_router(service: WorkspaceService | None, security: ApiSecurity) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces", tags=["workspace"])

    def configured() -> WorkspaceService:
        if service is None:
            raise HTTPException(
                503, "workspace service unavailable", headers={"Cache-Control": "no-store"}
            )
        return service

    @router.get("")
    def accessible(actor: Annotated[Session, Depends(security.actor)]) -> list[dict[str, object]]:
        try:
            return [workspace_view(access) for access in configured().accessible(actor.user_id)]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{workspace_id}")
    def context(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        return workspace_view(access)

    @router.get("/{workspace_id}/members")
    def members(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.MEMBER_MANAGE))],
    ) -> list[dict[str, object]]:
        try:
            return [
                member_view(member)
                for member in configured().members(access.user_id, access.workspace.workspace_id)
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.put("/{workspace_id}/members/{user_id}")
    def set_member(
        request: Request,
        user_id: UUID,
        change: MemberChange,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.MEMBER_MANAGE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            return member_view(
                configured().set_member(
                    access.user_id,
                    access.workspace.workspace_id,
                    user_id,
                    role=change.role,
                    active=change.active,
                    audit_context=request_context(request, "api.membership.change"),
                )
            )
        except PortError as error:
            raise public_error(error) from None

    return router
