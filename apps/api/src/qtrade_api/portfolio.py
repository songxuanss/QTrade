"""Authorized portfolio summary query."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.portfolio import PortfolioService
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.portfolio import PortfolioSummary
from qtrade_domain.ports import PortError


def portfolio_router(service: PortfolioService | None, security: ApiSecurity) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/portfolio", tags=["portfolio"])

    @router.get("")
    def summary(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        if service is None:
            raise HTTPException(404, "portfolio unavailable", headers={"Cache-Control": "no-store"})
        try:
            result = service.summary(access)
        except PortError as error:
            raise public_error(error) from None
        if result is None:
            raise HTTPException(404, "portfolio unavailable", headers={"Cache-Control": "no-store"})
        return _payload(result)

    return router


def _payload(result: PortfolioSummary) -> dict[str, object]:
    return {
        "workspace_id": str(result.workspace_id),
        "account_id": result.account_id,
        "currency": result.currency,
        "cash": format(result.cash, "f"),
        "equity": format(result.equity, "f"),
        "unrealized_pnl": format(result.unrealized_pnl, "f"),
        "gross_exposure": format(result.gross_exposure, "f"),
        "concentration": format(result.concentration, "f"),
        "drawdown": format(result.drawdown, "f"),
        "open_orders": result.open_orders,
        "positions": [
            {
                "instrument_id": item.instrument_id,
                "quantity": format(item.quantity, "f"),
                "mark_price": format(item.mark_price, "f"),
                "market_value": format(item.market_value, "f"),
                "unrealized_pnl": format(item.unrealized_pnl, "f"),
            }
            for item in result.positions
        ],
        "as_of": result.as_of.isoformat(),
        "source": result.source,
    }
