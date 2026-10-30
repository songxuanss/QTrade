"""Workspace portfolio snapshots and provider-neutral portfolio access."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from qtrade_domain.ports import ErrorCode, PortError


def _decimal(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or (positive and value <= 0):
        raise PortError(ErrorCode.INVALID_INPUT, f"invalid portfolio {name}")


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    instrument_id: str
    quantity: Decimal
    average_price: Decimal
    mark_price: Decimal
    contract_multiplier: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not 1 <= len(self.instrument_id) <= 256:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid portfolio instrument")
        _decimal(self.quantity, "quantity")
        if self.quantity == 0:
            raise PortError(ErrorCode.INVALID_INPUT, "portfolio position quantity cannot be zero")
        _decimal(self.average_price, "average price", positive=True)
        _decimal(self.mark_price, "mark price", positive=True)
        _decimal(self.contract_multiplier, "contract multiplier", positive=True)

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.mark_price * self.contract_multiplier

    @property
    def unrealized_pnl(self) -> Decimal:
        return (self.mark_price - self.average_price) * self.quantity * self.contract_multiplier


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    workspace_id: UUID
    account_id: str
    currency: str
    cash: Decimal
    positions: tuple[PortfolioPosition, ...]
    peak_equity: Decimal
    open_orders: int
    as_of: datetime
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, UUID):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid portfolio workspace")
        if not isinstance(self.account_id, str) or not 1 <= len(self.account_id) <= 128:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid portfolio account")
        if not isinstance(self.currency, str) or not 3 <= len(self.currency) <= 10:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid portfolio currency")
        _decimal(self.cash, "cash")
        _decimal(self.peak_equity, "peak equity", positive=True)
        if (
            not isinstance(self.positions, tuple)
            or any(not isinstance(item, PortfolioPosition) for item in self.positions)
            or len({item.instrument_id for item in self.positions}) != len(self.positions)
            or type(self.open_orders) is not int
            or self.open_orders < 0
            or not isinstance(self.as_of, datetime)
            or self.as_of.tzinfo is None
            or self.as_of.utcoffset() is None
            or not isinstance(self.source, str)
            or not self.source
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid portfolio snapshot")


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    workspace_id: UUID
    account_id: str
    currency: str
    cash: Decimal
    equity: Decimal
    unrealized_pnl: Decimal
    gross_exposure: Decimal
    concentration: Decimal
    drawdown: Decimal
    open_orders: int
    positions: tuple[PortfolioPosition, ...]
    as_of: datetime
    source: str


class PortfolioPort(Protocol):
    def get(self, workspace_id: UUID) -> PortfolioSnapshot | None: ...
