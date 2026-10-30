"""Authorized immutable Bar imports and scoped OHLCV queries."""

import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.market_data import MarketDataService
from qtrade_contracts import Bar
from qtrade_contracts.models import BarGranularity
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import BarQuery, PortError


def market_data_router(service: MarketDataService | None, security: ApiSecurity) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/workspaces/{workspace_id}/datasets/{dataset_id}/bars", tags=["market-data"]
    )

    def configured() -> MarketDataService:
        if service is None:
            raise HTTPException(503, "market data service unavailable")
        return service

    @router.post("")
    async def import_bars(
        dataset_id: UUID,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.DATA_MANAGE, mutate=True))
        ],
    ) -> dict[str, object]:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 5_000_000:
                raise HTTPException(413, "bar payload too large")
        try:
            payload = json.loads(body)
            if (
                not isinstance(payload, dict)
                or set(payload) != {"bars"}
                or not isinstance(payload["bars"], list)
                or len(payload["bars"]) > 10_000
            ):
                raise ValueError("invalid bar import")
            bars = tuple(Bar.model_validate_json(json.dumps(item)) for item in payload["bars"])
        except (ValueError, TypeError):
            raise HTTPException(400, "invalid bar import") from None
        try:
            configured().write_bars(access, dataset_id, bars)
        except PortError as error:
            raise public_error(error) from None
        return {"dataset_id": str(dataset_id), "record_count": len(bars)}

    @router.get("")
    def query_bars(
        dataset_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        instrument_id: Annotated[str, Query(min_length=1, max_length=256)],
        granularity: BarGranularity,
        start: datetime,
        end: datetime,
        limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
        cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    ) -> dict[str, object]:
        try:
            query = BarQuery(
                access.workspace.workspace_id,
                dataset_id,
                instrument_id,
                granularity,
                start,
                end,
                limit,
                cursor,
            )
            page = configured().query_bars(access, query)
        except PortError as error:
            raise public_error(error) from None
        return {
            "dataset_id": str(dataset_id),
            "timezone": "UTC",
            "start": start.astimezone(UTC).isoformat(),
            "end": end.astimezone(UTC).isoformat(),
            "bars": [bar.model_dump(mode="json") for bar in page.bars],
            "next_cursor": page.next_cursor,
        }

    return router
