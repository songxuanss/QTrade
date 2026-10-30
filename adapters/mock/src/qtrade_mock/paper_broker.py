"""Deterministic Paper Broker using injected prices and portfolio-local balances."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from qtrade_contracts import OrderCommand
from qtrade_domain.paper_matching import settle_paper_order
from qtrade_domain.ports import (
    BrokerOrder,
    BrokerPosition,
    BrokerSnapshot,
    ErrorCode,
    PortError,
)

PriceSource = Callable[[UUID, str], Decimal]


class PaperBroker:
    def __init__(
        self,
        price_source: PriceSource,
        *,
        initial_cash: Decimal = Decimal("1000000"),
        slippage_bps: Decimal = Decimal("5"),
        commission_bps: Decimal = Decimal("3"),
        currency: str = "CNY",
    ) -> None:
        if (
            not isinstance(initial_cash, Decimal)
            or not initial_cash.is_finite()
            or initial_cash < 0
            or not isinstance(slippage_bps, Decimal)
            or not slippage_bps.is_finite()
            or slippage_bps < 0
            or slippage_bps >= Decimal("10000")
            or not isinstance(commission_bps, Decimal)
            or not commission_bps.is_finite()
            or commission_bps < 0
            or len(currency) != 3
            or not currency.isalpha()
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid Paper Broker configuration")
        self._price_source = price_source
        self._initial_cash = initial_cash
        self._slippage = slippage_bps / Decimal("10000")
        self._commission = commission_bps / Decimal("10000")
        self._currency = currency.upper()
        self._cash: dict[tuple[UUID, UUID], Decimal] = {}
        self._positions: dict[tuple[UUID, UUID, str], BrokerPosition] = {}
        self._orders: dict[UUID, BrokerOrder] = {}
        self._commands: dict[UUID, OrderCommand] = {}

    def submit(self, command: OrderCommand) -> BrokerOrder:
        if command.decision.outcome != "allow":
            raise PortError(ErrorCode.REJECTED, "Paper Broker requires an allowed risk decision")
        key = command.idempotency_key
        if key in self._commands:
            if self._commands[key] != command:
                raise PortError(ErrorCode.CONFLICT, "Paper Broker idempotency key reused")
            return self._orders[key]

        intent = command.intent
        account_key = (command.workspace_id, intent.account_id)
        position_key = (*account_key, intent.instrument_id)
        position = self._positions.get(
            position_key, BrokerPosition(intent.instrument_id, Decimal(0), Decimal(0))
        )
        try:
            market = self._price_source(command.workspace_id, intent.instrument_id)
        except Exception:
            raise PortError(ErrorCode.UNAVAILABLE, "Paper Broker price unavailable") from None
        if not isinstance(market, Decimal) or not market.is_finite() or market <= 0:
            raise PortError(ErrorCode.UNAVAILABLE, "Paper Broker price unavailable")

        settlement = settle_paper_order(
            command,
            market=market,
            cash=self._cash.get(account_key, self._initial_cash),
            position=position,
            slippage_bps=self._slippage * Decimal("10000"),
            commission_bps=self._commission * Decimal("10000"),
        )
        self._cash[account_key] = settlement.cash
        if settlement.position.quantity:
            self._positions[position_key] = settlement.position
        else:
            self._positions.pop(position_key, None)
        self._commands[key] = command
        self._orders[key] = settlement.order
        return settlement.order

    def cancel(self, workspace_id: UUID, account_id: UUID, broker_order_id: str) -> BrokerOrder:
        for _key, order in self._orders.items():
            if (order.workspace_id, order.account_id, order.broker_order_id) == (
                workspace_id,
                account_id,
                broker_order_id,
            ):
                if order.status == "filled":
                    raise PortError(ErrorCode.CONFLICT, "filled Paper order cannot be cancelled")
                return order
        raise PortError(ErrorCode.NOT_FOUND, "Paper order not found")

    def snapshot(self, workspace_id: UUID, account_id: UUID) -> BrokerSnapshot:
        return BrokerSnapshot(
            workspace_id,
            account_id,
            datetime.now(UTC),
            self._currency,
            self._cash.get((workspace_id, account_id), self._initial_cash),
            tuple(
                position
                for (scope, account, _), position in self._positions.items()
                if (scope, account) == (workspace_id, account_id)
            ),
            tuple(
                order
                for order in self._orders.values()
                if (order.workspace_id, order.account_id) == (workspace_id, account_id)
            ),
        )
