"""Saved-watchlist snapshots from the latest published workspace dataset."""

from dataclasses import fields
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.market_watch import MarketWatchService
from qtrade_contracts.models import BarGranularity
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import PortError


def market_watch_router(service: MarketWatchService | None, security: ApiSecurity) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/workspaces/{workspace_id}/market-watch", tags=["market-watch"]
    )

    @router.get("")
    def snapshots(
        workspace_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        instrument_id: Annotated[list[str] | None, Query(max_length=50)] = None,
    ) -> list[dict[str, object]]:
        if service is None:
            raise HTTPException(
                503, "market watch unavailable", headers={"Cache-Control": "no-store"}
            )
        try:
            result = service.snapshots(access, tuple(instrument_id or ()))
            return [
                {
                    "instrument_id": item.instrument_id,
                    "dataset_id": item.dataset_id,
                    "granularity": item.granularity,
                    "available_granularities": list(item.available_granularities),
                    "latest": item.latest.model_dump(mode="json") if item.latest else None,
                    "previous": item.previous.model_dump(mode="json") if item.previous else None,
                }
                for item in result
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/chart")
    def chart(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        instrument_id: Annotated[str, Query(min_length=1, max_length=256)],
        granularity: BarGranularity,
        days: Annotated[int, Query(ge=1, le=30)] = 7,
        dataset_id: UUID | None = None,
    ) -> dict[str, object]:
        if service is None:
            raise HTTPException(
                503, "market chart unavailable", headers={"Cache-Control": "no-store"}
            )
        try:
            chart = service.chart(access, instrument_id, granularity, days, dataset_id=dataset_id)
            return {
                "instrument_id": instrument_id,
                "granularity": granularity,
                "days": days,
                "dataset_id": str(dataset_id) if dataset_id is not None else None,
                "bars": [item.model_dump(mode="json") for item in chart.bars],
                "indicators": {
                    field.name: [
                        format(value, "f") if value is not None else None
                        for value in getattr(chart.indicators, field.name)
                    ]
                    for field in fields(chart.indicators)
                },
            }
        except PortError as error:
            raise public_error(error) from None

    return router
