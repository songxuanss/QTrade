"""Order lifecycle invariants shared by Paper and Live execution contexts."""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from qtrade_contracts import Order, OrderEvent, OrderStatus
from qtrade_domain.ports import ErrorCode, PortError

_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    "created": frozenset({"risk_approved", "rejected"}),
    "risk_approved": frozenset({"submitted", "rejected", "failed"}),
    "submitted": frozenset(
        {
            "partially_filled",
            "filled",
            "cancel_pending",
            "cancelled",
            "rejected",
            "timeout",
            "failed",
        }
    ),
    "partially_filled": frozenset(
        {
            "partially_filled",
            "filled",
            "cancel_pending",
            "cancelled",
            "rejected",
            "timeout",
            "failed",
        }
    ),
    "cancel_pending": frozenset(
        {"submitted", "partially_filled", "filled", "cancelled", "timeout", "failed"}
    ),
    "timeout": frozenset(
        {"submitted", "partially_filled", "filled", "cancelled", "rejected", "failed"}
    ),
    "filled": frozenset(),
    "cancelled": frozenset(),
    "rejected": frozenset(),
    "failed": frozenset(),
}


def transition_order(
    current: Order,
    target: OrderStatus,
    *,
    occurred_at: datetime,
    source: Literal["risk", "broker", "user", "recovery", "system"],
    event_id: UUID,
    filled_quantity: Decimal | None = None,
    average_fill_price: Decimal | None = None,
    reason: str | None = None,
    reconciled: bool = False,
) -> tuple[Order, OrderEvent]:
    """Return the next immutable order and event after validating state and fills."""
    if target not in _TRANSITIONS[current.status]:
        raise PortError(
            ErrorCode.CONFLICT,
            f"illegal order transition {current.status} -> {target}",
        )
    if current.status == "timeout" and not reconciled:
        raise PortError(ErrorCode.CONFLICT, "timed-out orders require reconciliation first")
    if current.status == "cancel_pending" and target == "submitted" and not reconciled:
        raise PortError(ErrorCode.CONFLICT, "cancel rejection requires broker reconciliation")
    next_filled = current.filled_quantity if filled_quantity is None else filled_quantity
    next_average = current.average_fill_price if average_fill_price is None else average_fill_price
    if (
        occurred_at.tzinfo is None
        or occurred_at < current.updated_at
        or next_filled < current.filled_quantity
        or next_filled > current.quantity
        or (next_filled > 0 and next_average is None)
        or (next_filled == 0 and next_average is not None)
    ):
        raise PortError(ErrorCode.INVALID_INPUT, "order transition has invalid fill data or time")
    next_order = Order.model_validate(
        {
            **current.model_dump(),
            "status": target,
            "filled_quantity": next_filled,
            "average_fill_price": next_average,
            "updated_at": occurred_at,
            "revision": current.revision + 1,
            "terminal_reason": reason,
        }
    )
    event = OrderEvent(
        schema_version=1,
        workspace_id=current.workspace_id,
        event_id=event_id,
        order_id=current.order_id,
        revision=next_order.revision,
        from_status=current.status,
        to_status=target,
        occurred_at=occurred_at,
        source=source,
        filled_quantity=next_filled,
        reason=reason,
    )
    return next_order, event


def assert_idempotency_keys_unique(keys: Iterable[UUID]) -> None:
    """Reject duplicate command keys before writing or dispatching any order."""
    values = tuple(keys)
    if any(not isinstance(value, UUID) for value in values) or len(values) != len(set(values)):
        raise PortError(ErrorCode.CONFLICT, "order idempotency keys must be unique")
