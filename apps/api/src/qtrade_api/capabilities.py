"""Workspace-scoped capability discovery and selection validation."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.capabilities import CapabilityRegistry
from qtrade_contracts.models import Granularity, OrderType
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.capabilities import AdapterKind, CapabilityRequest
from qtrade_domain.ports import ErrorCode, PortError


def capability_router(registry: CapabilityRegistry[object], security: ApiSecurity) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/adapters", tags=["capabilities"])

    @router.get("")
    def list_adapters(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        return [asdict(descriptor) for descriptor in registry.descriptors()]

    @router.get("/{adapter_id}/availability")
    def availability(
        adapter_id: str,
        kind: AdapterKind,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        market: str | None = None,
        granularity: Granularity | None = None,
        order_type: OrderType | None = None,
        trading_session: str | None = None,
        live: bool = False,
    ) -> dict[str, object]:
        try:
            selected = registry.require(
                adapter_id,
                CapabilityRequest(kind, market, granularity, order_type, trading_session, live),
            )
        except PortError as error:
            if error.code == ErrorCode.UNSUPPORTED:
                raise HTTPException(
                    422,
                    "adapter does not support requested capability",
                    headers={"Cache-Control": "no-store"},
                ) from None
            raise public_error(error) from None
        return {"available": True, "adapter_id": selected.descriptor.adapter_id}

    return router
