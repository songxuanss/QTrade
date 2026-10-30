"""Broker versus local account reconciliation and discrepancy report contracts."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from qtrade_domain.ports import BrokerOrder, BrokerPosition, BrokerSnapshot, ErrorCode, PortError

ReconciliationCategory = Literal["cash", "position", "order", "fill", "currency"]
ReconciliationStatus = Literal["matched", "mismatch"]


@dataclass(frozen=True)
class LocalAccountSnapshot:
    workspace_id: UUID
    account_id: UUID
    as_of: datetime
    currency: str
    cash: Decimal
    positions: tuple[BrokerPosition, ...]
    orders: tuple[BrokerOrder, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.account_id, UUID)
            or not isinstance(self.as_of, datetime)
            or self.as_of.tzinfo is None
            or not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not isinstance(self.cash, Decimal)
            or not self.cash.is_finite()
            or self.cash < 0
            or not isinstance(self.positions, tuple)
            or not isinstance(self.orders, tuple)
            or any(
                not isinstance(item, BrokerPosition)
                or not isinstance(item.quantity, Decimal)
                or not item.quantity.is_finite()
                or item.quantity < 0
                or not isinstance(item.average_price, Decimal)
                or not item.average_price.is_finite()
                or item.average_price < 0
                for item in self.positions
            )
            or any(
                not isinstance(item, BrokerOrder)
                or item.workspace_id != self.workspace_id
                or item.account_id != self.account_id
                for item in self.orders
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid local account snapshot")


@dataclass(frozen=True)
class ReconciliationDifference:
    category: ReconciliationCategory
    resource_id: str
    local_value: str
    broker_value: str
    summary: str


@dataclass(frozen=True)
class ReconciliationTolerance:
    cash: Decimal
    quantity: Decimal
    price: Decimal

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value < 0
            for value in (self.cash, self.quantity, self.price)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid reconciliation tolerance")


@dataclass(frozen=True)
class ReconciliationReport:
    report_id: UUID
    workspace_id: UUID
    account_id: UUID
    reconciled_at: datetime
    status: ReconciliationStatus
    differences: tuple[ReconciliationDifference, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.report_id, UUID)
            or not isinstance(self.workspace_id, UUID)
            or not isinstance(self.account_id, UUID)
            or not isinstance(self.reconciled_at, datetime)
            or self.reconciled_at.tzinfo is None
            or self.status not in {"matched", "mismatch"}
            or not isinstance(self.differences, tuple)
            or any(not isinstance(item, ReconciliationDifference) for item in self.differences)
            or (self.status == "matched") != (len(self.differences) == 0)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid reconciliation report")

    @property
    def blocks_live_orders(self) -> bool:
        return self.status == "mismatch"


class ReconciliationReportStore(Protocol):
    def record(self, report: ReconciliationReport) -> ReconciliationReport: ...

    def latest(self, workspace_id: UUID, account_id: UUID) -> ReconciliationReport | None: ...


class AccountTradingGateStore(Protocol):
    def block(
        self,
        workspace_id: UUID,
        account_id: UUID,
        report_id: UUID,
        occurred_at: datetime,
    ) -> None: ...

    def is_blocked(self, workspace_id: UUID, account_id: UUID) -> bool: ...


def reconcile_account(
    local: LocalAccountSnapshot,
    broker: BrokerSnapshot,
    *,
    tolerance: ReconciliationTolerance,
    report_id: UUID,
    reconciled_at: datetime,
) -> ReconciliationReport:
    if (
        not isinstance(broker, BrokerSnapshot)
        or local.workspace_id != broker.workspace_id
        or local.account_id != broker.account_id
        or reconciled_at.tzinfo is None
        or reconciled_at.utcoffset() is None
        or local.as_of.utcoffset() is None
        or broker.as_of.utcoffset() is None
        or not isinstance(broker.cash, Decimal)
        or not broker.cash.is_finite()
        or broker.cash < 0
        or not isinstance(broker.positions, tuple)
        or not isinstance(broker.orders, tuple)
        or any(
            not isinstance(item, BrokerPosition)
            or not isinstance(item.quantity, Decimal)
            or not item.quantity.is_finite()
            or item.quantity < 0
            or not isinstance(item.average_price, Decimal)
            or not item.average_price.is_finite()
            or item.average_price < 0
            for item in broker.positions
        )
        or any(
            not isinstance(item, BrokerOrder)
            or item.workspace_id != broker.workspace_id
            or item.account_id != broker.account_id
            for item in broker.orders
        )
    ):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid account reconciliation inputs")
    differences: list[ReconciliationDifference] = []
    if local.currency != broker.currency:
        differences.append(
            ReconciliationDifference(
                "currency", "account", local.currency, broker.currency, "account currency differs"
            )
        )
    if abs(local.cash - broker.cash) > tolerance.cash:
        differences.append(
            ReconciliationDifference(
                "cash", "account", str(local.cash), str(broker.cash), "cash balance differs"
            )
        )
    local_positions = {item.instrument_id: item for item in local.positions}
    broker_positions = {item.instrument_id: item for item in broker.positions}
    if len(local_positions) != len(local.positions) or len(broker_positions) != len(
        broker.positions
    ):
        raise PortError(ErrorCode.INVALID_INPUT, "duplicate position in reconciliation snapshot")
    for instrument_id in sorted(local_positions.keys() | broker_positions.keys()):
        left = local_positions.get(
            instrument_id, BrokerPosition(instrument_id, Decimal(0), Decimal(0))
        )
        right = broker_positions.get(
            instrument_id, BrokerPosition(instrument_id, Decimal(0), Decimal(0))
        )
        if abs(left.quantity - right.quantity) > tolerance.quantity:
            differences.append(
                ReconciliationDifference(
                    "position",
                    instrument_id,
                    str(left.quantity),
                    str(right.quantity),
                    "position quantity differs",
                )
            )
        elif left.quantity != 0 and abs(left.average_price - right.average_price) > tolerance.price:
            differences.append(
                ReconciliationDifference(
                    "position",
                    instrument_id,
                    str(left.average_price),
                    str(right.average_price),
                    "position average price differs",
                )
            )
    local_orders = _orders_by_id(local.orders)
    broker_orders = {item.broker_order_id: item for item in broker.orders}
    for order_id in sorted(local_orders.keys() | broker_orders.keys()):
        local_order, broker_order = local_orders.get(order_id), broker_orders.get(order_id)
        if local_order is None or broker_order is None:
            differences.append(
                ReconciliationDifference(
                    "order",
                    order_id,
                    "missing" if local_order is None else local_order.status,
                    "missing" if broker_order is None else broker_order.status,
                    "order exists on only one side",
                )
            )
            continue
        if (
            local_order.status != broker_order.status
            or abs(local_order.filled_quantity - broker_order.filled_quantity) > tolerance.quantity
        ):
            differences.append(
                ReconciliationDifference(
                    "fill",
                    order_id,
                    f"{local_order.status}:{local_order.filled_quantity}",
                    f"{broker_order.status}:{broker_order.filled_quantity}",
                    "order status or cumulative fill differs",
                )
            )
        if abs(local_order.quantity - broker_order.quantity) > tolerance.quantity:
            differences.append(
                ReconciliationDifference(
                    "order",
                    order_id,
                    str(local_order.quantity),
                    str(broker_order.quantity),
                    "order quantity differs",
                )
            )
        if (
            local_order.average_fill_price is not None
            and broker_order.average_fill_price is not None
            and abs(local_order.average_fill_price - broker_order.average_fill_price)
            > tolerance.price
        ):
            differences.append(
                ReconciliationDifference(
                    "fill",
                    order_id,
                    str(local_order.average_fill_price),
                    str(broker_order.average_fill_price),
                    "average fill price differs",
                )
            )
    return ReconciliationReport(
        report_id,
        local.workspace_id,
        local.account_id,
        reconciled_at,
        "mismatch" if differences else "matched",
        tuple(differences),
    )


def _orders_by_id(orders: tuple[BrokerOrder, ...]) -> dict[str, BrokerOrder]:
    if any(not isinstance(order, BrokerOrder) for order in orders):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid local broker order snapshot")
    values = {order.broker_order_id: order for order in orders}
    if len(values) != len(orders):
        raise PortError(ErrorCode.INVALID_INPUT, "duplicate order in reconciliation snapshot")
    return values
