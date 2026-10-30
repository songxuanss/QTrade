"""Authorized realtime market-stream health queries and WebSocket updates."""

import asyncio
from contextlib import suppress
from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, WebSocket, WebSocketDisconnect

from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.realtime import RealtimeMarketDataService
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.realtime import MarketStreamHealth, MarketStreamKey


def realtime_router(service: RealtimeMarketDataService | None, security: ApiSecurity) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/workspaces/{workspace_id}/market-streams", tags=["market-data"]
    )

    def configured() -> RealtimeMarketDataService:
        if service is None:
            raise HTTPException(
                503, "market stream health unavailable", headers={"Cache-Control": "no-store"}
            )
        return service

    @router.get("")
    def list_health(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, object]]:
        try:
            rows = configured().list(access, limit)
        except PortError as error:
            raise public_error(error) from None
        return [_payload(row) for row in rows]

    @router.get("/{source}/{instrument_id}")
    def get_health(
        source: Annotated[str, Path(min_length=1, max_length=256)],
        instrument_id: Annotated[str, Path(min_length=1, max_length=256)],
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        try:
            return _payload(
                configured().status(
                    access, MarketStreamKey(access.workspace.workspace_id, source, instrument_id)
                )
            )
        except PortError as error:
            raise public_error(error) from None

    @router.websocket("/ws/{source}/{instrument_id}")
    async def stream_health(
        websocket: WebSocket,
        workspace_id: UUID,
        source: Annotated[str, Path(min_length=1, max_length=256)],
        instrument_id: Annotated[str, Path(min_length=1, max_length=256)],
    ) -> None:
        try:
            access = security.websocket_workspace(websocket, workspace_id, Permission.READ)
        except PortError as error:
            await websocket.close(code=4403 if error.code == ErrorCode.REJECTED else 4401)
            return
        await websocket.accept()
        previous: dict[str, object] | None = None
        last_authorized = monotonic()
        key = MarketStreamKey(workspace_id, source, instrument_id)
        try:
            while True:
                try:
                    if monotonic() - last_authorized >= 15:
                        access = security.websocket_workspace(
                            websocket, workspace_id, Permission.READ
                        )
                        last_authorized = monotonic()
                    health = configured().status(access, key)
                    payload = _payload(health)
                except PortError as error:
                    if error.code == ErrorCode.NOT_FOUND:
                        payload = {
                            "status": "unconfigured",
                            "source": source,
                            "instrument_id": instrument_id,
                        }
                    else:
                        payload = {
                            "status": "unavailable",
                            "source": source,
                            "instrument_id": instrument_id,
                        }
                if payload != previous:
                    await websocket.send_json(payload)
                    previous = payload
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            return
        except Exception:
            with suppress(Exception):
                await websocket.close(code=1011)

    return router


def _payload(health: MarketStreamHealth) -> dict[str, object]:
    return {
        "workspace_id": str(health.key.workspace_id),
        "source": health.key.source,
        "instrument_id": health.key.instrument_id,
        "status": health.status,
        "updated_at": health.updated_at.isoformat(),
        "heartbeat_at": health.heartbeat_at.isoformat() if health.heartbeat_at else None,
        "last_event_at": health.last_event_at.isoformat() if health.last_event_at else None,
        "last_sequence": health.last_sequence,
        "latest_lag_ms": health.latest_lag_ms,
        "max_lag_ms": health.max_lag_ms,
        "out_of_order_count": health.out_of_order_count,
        "reconnect_count": health.reconnect_count,
        "next_reconnect_at": (
            health.next_reconnect_at.isoformat() if health.next_reconnect_at else None
        ),
        "last_error_code": health.last_error_code.value if health.last_error_code else None,
    }
