"""Scoped calendar imports and deterministic session/market-state queries."""

from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.calendars import CalendarService
from qtrade_contracts import CalendarSchedule
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.calendar_contracts import from_contract
from qtrade_domain.ports import ErrorCode, PortError


def calendar_router(service: CalendarService | None, security: ApiSecurity) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/calendars", tags=["calendars"])

    def configured() -> CalendarService:
        if service is None:
            raise HTTPException(503, "calendar service unavailable")
        return service

    @router.post("/import")
    async def import_calendar(
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, object]:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 500_000:
                raise HTTPException(413, "calendar payload too large")
        try:
            value = from_contract(CalendarSchedule.model_validate_json(body))
        except (ValueError, TypeError):
            raise HTTPException(400, "invalid calendar schedule") from None
        try:
            if value.workspace_id != access.workspace.workspace_id:
                raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
            configured().put(access, value.calendar)
            return {
                "calendar_id": value.calendar.calendar_id,
                "calendar_version": value.calendar.version,
            }
        except PortError as error:
            raise public_error(error) from None

    @router.get("/sessions")
    def sessions(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        calendar_id: Annotated[str, Query(min_length=1, max_length=256)],
        start: date,
        end: date,
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> list[dict[str, object]]:
        try:
            return [
                asdict(window)
                for window in configured().sessions(access, calendar_id, start, end, version)
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/state")
    def state(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        calendar_id: Annotated[str, Query(min_length=1, max_length=256)],
        at: datetime,
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> dict[str, object]:
        try:
            return asdict(configured().state(access, calendar_id, at, version))
        except PortError as error:
            raise public_error(error) from None

    return router
