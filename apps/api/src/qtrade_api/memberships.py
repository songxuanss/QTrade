"""Workspace universe membership intervals: evidence-backed effective windows."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, Field

from qtrade_api.audit_context import request_context
from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.universe_memberships import (
    MembershipSubmission,
    UniverseMembershipService,
)
from qtrade_domain.audit_port import AuditDetails, AuditRecord, AuditStorePort, contextualize
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import PortError


class MembershipInput(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=128)
    effective_from: AwareDatetime
    effective_to: AwareDatetime
    evidence: str = Field(min_length=1, max_length=500)
    source: Literal["workspace.definition", "supplier.membership"] = "workspace.definition"


def membership_router(
    service: UniverseMembershipService | None,
    security: ApiSecurity,
    audit: AuditStorePort | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/workspaces/{workspace_id}/data/memberships", tags=["memberships"]
    )

    def configured() -> UniverseMembershipService:
        if service is None:
            raise HTTPException(503, "membership service unavailable")
        return service

    @router.get("")
    def list_memberships(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        instrument_id: Annotated[str | None, Query(max_length=128)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[dict[str, object]]:
        try:
            return [
                item.model_dump(mode="json")
                for item in configured().list(access, instrument_id, limit)
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.post("", status_code=201)
    def create_membership(
        body: MembershipInput,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            membership = configured().register(
                access,
                MembershipSubmission(
                    body.instrument_id,
                    body.effective_from,
                    body.effective_to,
                    body.evidence,
                    body.source,
                ),
            )
            _audit(audit, access, request, "membership.create", membership.membership_id)
            return membership.model_dump(mode="json")
        except PortError as error:
            raise public_error(error) from None

    return router


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
