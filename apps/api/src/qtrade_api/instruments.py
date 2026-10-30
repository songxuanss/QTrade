"""Authenticated master-data imports and scoped queries; no raw validation echoes."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.calendars import CalendarService
from qtrade_application.instruments import InstrumentService
from qtrade_contracts import Instrument
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.instruments import InstrumentQuery
from qtrade_domain.ports import ErrorCode, PortError


def instrument_router(
    service: InstrumentService | None,
    security: ApiSecurity,
    calendars: CalendarService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/instruments", tags=["instruments"])

    def configured() -> InstrumentService:
        if service is None:
            raise HTTPException(
                503, "instrument service unavailable", headers={"Cache-Control": "no-store"}
            )
        return service

    @router.post("/import")
    async def import_instruments(
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> list[dict[str, object]]:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 500_000:
                raise HTTPException(
                    413, "instrument payload too large", headers={"Cache-Control": "no-store"}
                )
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) != {"instruments"}:
                raise ValueError("invalid instrument import")
            if (
                not isinstance(payload["instruments"], list)
                or not 1 <= len(payload["instruments"]) <= 200
            ):
                raise ValueError("invalid instrument batch")
            items = tuple(
                Instrument.model_validate_json(json.dumps(item)) for item in payload["instruments"]
            )
        except (ValueError, TypeError):
            raise HTTPException(
                400, "invalid instrument import", headers={"Cache-Control": "no-store"}
            ) from None
        try:
            return [asdict(result) for result in configured().import_instruments(access, items)]
        except PortError as error:
            raise public_error(error) from None

    @router.get("")
    def query(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        instrument_id: str | None = None,
        name: str | None = None,
        exchange: str | None = None,
        market: str | None = None,
        enabled: Literal["true", "false", "all"] = "true",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        after: str | None = None,
    ) -> dict[str, object]:
        try:
            page = configured().query(
                access,
                InstrumentQuery(
                    access.workspace.workspace_id,
                    instrument_id,
                    name,
                    exchange,
                    market,
                    None if enabled == "all" else enabled == "true",
                    limit,
                    after,
                ),
            )
        except PortError as error:
            raise public_error(error) from None
        return {
            "instruments": [item.model_dump(mode="json") for item in page.instruments],
            "next_after": page.next_after,
        }

    @router.get("/conflicts")
    def conflicts(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        try:
            return [
                {
                    "issue_id": str(issue.issue_id),
                    "workspace_id": str(issue.workspace_id),
                    "instrument_id": issue.instrument_id,
                    "status": issue.status,
                    "detected_at": issue.detected_at.isoformat(),
                    "existing": issue.existing.model_dump(mode="json"),
                    "proposed": issue.proposed.model_dump(mode="json"),
                }
                for issue in configured().conflicts(access)
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/market-state")
    def market_state(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        instrument_id: Annotated[str, Query(min_length=1, max_length=256)],
        at: datetime,
        version: Annotated[int | None, Query(ge=1)] = None,
    ) -> dict[str, object]:
        try:
            items = (
                configured()
                .query(
                    access,
                    InstrumentQuery(
                        access.workspace.workspace_id, instrument_id=instrument_id, enabled=None
                    ),
                )
                .instruments
            )
            if not items:
                raise PortError(ErrorCode.NOT_FOUND, "instrument unavailable")
            if calendars is None:
                raise PortError(ErrorCode.UNAVAILABLE, "calendar service unavailable")
            return asdict(calendars.instrument_state(access, items[0], at, version))
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{instrument_id:path}")
    def detail(
        instrument_id: str,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            items = (
                configured()
                .query(
                    access,
                    InstrumentQuery(
                        access.workspace.workspace_id,
                        instrument_id=instrument_id,
                        enabled=None,
                    ),
                )
                .instruments
            )
            if not items:
                raise PortError(ErrorCode.NOT_FOUND, "instrument unavailable")
            return items[0].model_dump(mode="json")
        except PortError as error:
            raise public_error(error) from None

    return router
