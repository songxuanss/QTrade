"""Durable order command and event journal used for broker recovery."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from qtrade_contracts import Order, OrderCommand, OrderEvent
from qtrade_domain.ports import BrokerOrder, ErrorCode, PortError


@dataclass(frozen=True)
class OrderExecutionRecord:
    command: OrderCommand
    order: Order
    events: tuple[OrderEvent, ...]
    broker_order: BrokerOrder | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.command, OrderCommand)
            or not isinstance(self.order, Order)
            or not isinstance(self.events, tuple)
            or not self.events
            or any(not isinstance(event, OrderEvent) for event in self.events)
            or self.command.workspace_id != self.order.workspace_id
            or self.command.intent.intent_id != self.order.intent_id
            or self.command.command_id != self.order.command_id
            or self.command.idempotency_key != self.order.idempotency_key
            or any(
                event.workspace_id != self.order.workspace_id
                or event.order_id != self.order.order_id
                for event in self.events
            )
            or self.events[-1].revision != self.order.revision
            or len(self.events) != self.order.revision
            or tuple(event.revision for event in self.events)
            != tuple(range(1, self.order.revision + 1))
            or (self.broker_order is not None and self.order.status == "submitted")
            or (self.broker_order is None and self.order.status in {"filled", "rejected"})
            or (
                self.broker_order is not None
                and (
                    self.broker_order.workspace_id != self.order.workspace_id
                    or self.broker_order.account_id != self.order.account_id
                    or self.broker_order.command_id != self.order.command_id
                )
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "inconsistent order execution record")


class OrderExecutionStore(Protocol):
    def get_for_intent(
        self, workspace_id: UUID, intent_id: UUID
    ) -> OrderExecutionRecord | None: ...

    def prepare(self, record: OrderExecutionRecord) -> OrderExecutionRecord: ...

    def update(self, record: OrderExecutionRecord) -> OrderExecutionRecord: ...
