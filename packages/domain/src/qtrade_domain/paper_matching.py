"""Pure Paper settlement rules shared by durable and in-memory broker adapters.

This immediate-fill model is a simulation, not an exchange matching engine.
Unmarketable limit and untriggered stop orders are rejected rather than parked.
"""

from dataclasses import dataclass
from decimal import Decimal

from qtrade_contracts import OrderCommand
from qtrade_domain.ports import BrokerOrder, BrokerPosition, ErrorCode, PortError


@dataclass(frozen=True)
class PaperSettlement:
    order: BrokerOrder
    cash: Decimal
    position: BrokerPosition


def settle_paper_order(
    command: OrderCommand,
    *,
    market: Decimal,
    cash: Decimal,
    position: BrokerPosition,
    slippage_bps: Decimal = Decimal("5"),
    commission_bps: Decimal = Decimal("3"),
) -> PaperSettlement:
    intent = command.intent
    if command.decision.outcome != "allow":
        raise PortError(ErrorCode.REJECTED, "Paper Broker requires an allowed risk decision")
    if (
        any(
            not isinstance(v, Decimal) or not v.is_finite()
            for v in (
                market,
                cash,
                position.quantity,
                position.average_price,
                slippage_bps,
                commission_bps,
            )
        )
        or market <= 0
        or cash < 0
        or position.quantity < 0
        or position.average_price < 0
        or (position.quantity > 0 and position.average_price <= 0)
        or position.instrument_id != intent.instrument_id
        or not Decimal(0) <= slippage_bps < Decimal(10000)
        or not Decimal(0) <= commission_bps < Decimal(10000)
    ):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid Paper settlement input")

    def reject(code: str) -> PaperSettlement:
        return PaperSettlement(
            BrokerOrder(
                command.workspace_id,
                intent.account_id,
                command.command_id,
                str(command.idempotency_key),
                "rejected",
                intent.quantity,
                Decimal(0),
                rejection_code=code,
            ),
            cash,
            position,
        )

    slipped = market * (
        Decimal(1) + (slippage_bps if intent.side == "buy" else -slippage_bps) / Decimal(10000)
    )
    price = slipped
    if intent.order_type in {"limit", "stop_limit"}:
        assert intent.limit_price is not None
        if (intent.side == "buy" and market > intent.limit_price) or (
            intent.side == "sell" and market < intent.limit_price
        ):
            return reject("limit_not_marketable")
        price = (
            min(slipped, intent.limit_price)
            if intent.side == "buy"
            else max(slipped, intent.limit_price)
        )
    if intent.order_type in {"stop", "stop_limit"}:
        assert intent.stop_price is not None
        if (intent.side == "buy" and market < intent.stop_price) or (
            intent.side == "sell" and market > intent.stop_price
        ):
            return reject("stop_not_triggered")
    gross = intent.quantity * price
    commission = gross * commission_bps / Decimal(10000)
    if intent.side == "buy":
        if gross + commission > cash:
            return reject("insufficient_paper_cash")
        quantity = position.quantity + intent.quantity
        average = (position.quantity * position.average_price + gross) / quantity
        cash -= gross + commission
    else:
        if intent.quantity > position.quantity:
            return reject("insufficient_paper_position")
        quantity = position.quantity - intent.quantity
        average = position.average_price if quantity else Decimal(0)
        cash += gross - commission
    return PaperSettlement(
        BrokerOrder(
            command.workspace_id,
            intent.account_id,
            command.command_id,
            str(command.idempotency_key),
            "filled",
            intent.quantity,
            intent.quantity,
            average_fill_price=price,
            commission=commission,
        ),
        cash,
        BrokerPosition(intent.instrument_id, quantity, average),
    )
