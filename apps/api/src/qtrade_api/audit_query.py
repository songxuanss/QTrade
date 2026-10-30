"""Read-only audit filters and restricted, no-cache exports."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from qtrade_api.audit_context import request_context
from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.audit_query import AuditQueryService
from qtrade_domain.audit_query import AuditQuery
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import PortError


def audit_query_router(service: AuditQueryService | None, security: ApiSecurity) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/audit", tags=["audit"])

    def filters(
        start: datetime | None = None,
        end: datetime | None = None,
        actor_id: UUID | None = None,
        action: Annotated[str | None, Query(max_length=256)] = None,
        target_id: UUID | None = None,
        result: Literal["success", "denied", "failure"] | None = None,
        trace_id: UUID | None = None,
        before: Annotated[int | None, Query(ge=1)] = None,
    ) -> AuditQuery:
        ending = end or datetime.now(UTC)
        try:
            return AuditQuery(
                start or ending - timedelta(days=7),
                ending,
                actor_id,
                action,
                target_id,
                result,
                trace_id,
                before,
            )
        except PortError as error:
            raise public_error(error) from None

    def configured() -> AuditQueryService:
        if service is None:
            raise HTTPException(503, "audit service unavailable")
        return service

    @router.get("")
    def search(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.AUDIT_READ))],
        query: Annotated[AuditQuery, Depends(filters)],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> dict[str, object]:
        try:
            return configured().search(access, query, limit)
        except PortError as error:
            raise public_error(error) from None

    @router.get("/export")
    def export(
        request: Request,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.AUDIT_EXPORT))],
        query: Annotated[AuditQuery, Depends(filters)],
    ) -> Response:
        try:
            content = configured().export(
                access, query, request_context(request, "api.audit.export")
            )
            return Response(
                content,
                media_type="text/csv",
                headers={
                    "Content-Disposition": 'attachment; filename="qtrade-audit.csv"',
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except PortError as error:
            raise public_error(error) from None

    return router
