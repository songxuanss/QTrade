from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest

from qtrade_contracts import Order, OrderStatus
from qtrade_domain.orders import (
    assert_idempotency_keys_unique,
    transition_order,
)
from qtrade_domain.ports import ErrorCode, PortError

FIXTURES = Path(__file__).parent / "fixtures/contracts"
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def order() -> Order:
    return Order.model_validate_json((FIXTURES / "Order.json").read_text())


def advance(
    current: Order,
    target: OrderStatus,
    seconds: int,
    *,
    source: Literal["risk", "broker", "user", "recovery", "system"] = "broker",
    **kwargs,
):
    return transition_order(
        current,
        target,
        occurred_at=NOW + timedelta(seconds=seconds),
        source=source,
        event_id=UUID(int=100 + seconds),
        **kwargs,
    )


def test_order_lifecycle_preserves_revision_and_allows_partial_fill_then_cancel():
    accepted, accepted_event = advance(order(), "risk_approved", 1, source="risk")
    submitted, submitted_event = advance(accepted, "submitted", 2)
    partial, partial_event = advance(
        submitted,
        "partially_filled",
        3,
        filled_quantity=Decimal("40"),
        average_fill_price=Decimal("10.2"),
    )
    cancelled, cancelled_event = advance(partial, "cancelled", 4, reason="user_cancel")

    assert [
        accepted_event.revision,
        submitted_event.revision,
        partial_event.revision,
        cancelled_event.revision,
    ] == [2, 3, 4, 5]
    assert cancelled.status == "cancelled"
    assert cancelled.filled_quantity == Decimal("40")
    assert cancelled_event.from_status == "partially_filled"


def test_order_transition_rejects_skipping_risk_and_fill_regression():
    with pytest.raises(PortError) as illegal:
        advance(order(), "submitted", 1)
    assert illegal.value.code == ErrorCode.CONFLICT
    submitted, _ = advance(advance(order(), "risk_approved", 1)[0], "submitted", 2)
    partial, _ = advance(
        submitted,
        "partially_filled",
        3,
        filled_quantity=Decimal("50"),
        average_fill_price=Decimal("10"),
    )
    with pytest.raises(PortError) as regression:
        advance(
            partial,
            "partially_filled",
            4,
            filled_quantity=Decimal("49"),
            average_fill_price=Decimal("10"),
        )
    assert regression.value.code == ErrorCode.INVALID_INPUT


def test_timeout_retry_requires_reconciliation_and_terminal_states_do_not_transition():
    created = order()
    approved, _ = advance(created, "risk_approved", 1)
    submitted, _ = advance(approved, "submitted", 2)
    timed_out, _ = advance(submitted, "timeout", 3)
    with pytest.raises(PortError, match="reconciliation"):
        advance(timed_out, "submitted", 4)
    recovered, _ = advance(timed_out, "submitted", 4, reconciled=True)
    assert recovered.status == "submitted"
    filled, _ = advance(
        recovered,
        "filled",
        5,
        filled_quantity=Decimal("100"),
        average_fill_price=Decimal("10.1"),
    )
    with pytest.raises(PortError):
        advance(filled, "cancel_pending", 6)


def test_idempotency_keys_are_unique_within_a_command_batch():
    first, second = UUID(int=1), UUID(int=2)
    assert_idempotency_keys_unique((first, second))
    with pytest.raises(PortError) as duplicate:
        assert_idempotency_keys_unique((first, second, first))
    assert duplicate.value.code == ErrorCode.CONFLICT
