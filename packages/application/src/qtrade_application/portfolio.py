"""Workspace-scoped portfolio aggregation over provider-neutral account data."""

from decimal import Decimal

from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.portfolio import PortfolioPort, PortfolioSummary
from qtrade_domain.ports import ErrorCode, PortError


class PortfolioService:
    def __init__(self, portfolios: PortfolioPort):
        self._portfolios = portfolios

    def summary(self, access: WorkspaceAccess) -> PortfolioSummary | None:
        access.require(Permission.READ)
        snapshot = self._portfolios.get(access.workspace.workspace_id)
        if snapshot is None:
            return None
        if snapshot.workspace_id != access.workspace.workspace_id:
            raise PortError(ErrorCode.NOT_FOUND, "portfolio unavailable")

        market_values = tuple(position.market_value for position in snapshot.positions)
        equity = snapshot.cash + sum(market_values, Decimal(0))
        exposure = sum((abs(value) for value in market_values), Decimal(0))
        concentration = (
            max((abs(value) for value in market_values), default=Decimal(0)) / exposure
            if exposure > 0
            else Decimal(0)
        )
        drawdown = max(Decimal(0), snapshot.peak_equity - equity) / snapshot.peak_equity
        pnl = sum((position.unrealized_pnl for position in snapshot.positions), Decimal(0))
        return PortfolioSummary(
            snapshot.workspace_id,
            snapshot.account_id,
            snapshot.currency,
            snapshot.cash,
            equity,
            pnl,
            exposure,
            concentration,
            drawdown,
            snapshot.open_orders,
            snapshot.positions,
            snapshot.as_of,
            snapshot.source,
        )
