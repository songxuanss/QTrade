from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from qtrade_contracts import OrderIntent, RiskDecision
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.risk_engine import (
    RiskCheckContext,
    RiskPolicy,
    evaluate_order_intent,
)
from qtrade_domain.risk_limits import (
    LimitProjection,
    LimitUsage,
    RiskLimit,
    calculate_limit_usage,
)

FIXTURES = Path(__file__).parent / "fixtures/contracts"
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def intent() -> OrderIntent:
    return OrderIntent.model_validate_json((FIXTURES / "OrderIntent.json").read_text())


def policy(
    *,
    quantity_step: Decimal = Decimal("1"),
    version: str = "risk.v1",
    max_exposure: Decimal = Decimal("1000"),
) -> RiskPolicy:
    return RiskPolicy(
        "account.default",
        version,
        Decimal("0.1"),
        quantity_step,
        5,
        Decimal("10"),
        max_exposure,
        Decimal("100"),
        Decimal("0.25"),
    )


def context(
    *,
    order_intent: OrderIntent | None = None,
    actor_authorized: bool = True,
    strategy_approved: bool = True,
    runtime_active: bool = True,
    market_open: bool = True,
    instrument_enabled: bool = True,
    planned_price: Decimal = Decimal("10"),
    reference_price: Decimal = Decimal("10"),
    orders_in_window: int = 0,
    current_position_quantity: Decimal = Decimal(0),
    current_exposure: Decimal = Decimal(0),
    daily_loss: Decimal = Decimal(0),
    current_drawdown: Decimal = Decimal(0),
    kill_switch_active: bool = False,
    existing_intent_ids: tuple[UUID, ...] = (),
    requires_approval: bool = False,
    limit_usages: tuple[LimitUsage, ...] = (),
) -> RiskCheckContext:
    return RiskCheckContext(
        intent=order_intent or intent(),
        actor_authorized=actor_authorized,
        strategy_approved=strategy_approved,
        runtime_active=runtime_active,
        market_open=market_open,
        instrument_enabled=instrument_enabled,
        planned_price=planned_price,
        reference_price=reference_price,
        orders_in_window=orders_in_window,
        current_position_quantity=current_position_quantity,
        current_exposure=current_exposure,
        daily_loss=daily_loss,
        current_drawdown=current_drawdown,
        kill_switch_active=kill_switch_active,
        existing_intent_ids=existing_intent_ids,
        requires_approval=requires_approval,
        limit_usages=limit_usages,
    )


def decision(check: RiskCheckContext, configured: RiskPolicy | None = None) -> RiskDecision:
    return evaluate_order_intent(
        configured or policy(),
        check,
        decided_at=NOW,
        decision_id=UUID(int=99),
    )


def test_order_intent_passes_all_checks_with_stable_version_and_hash() -> None:
    first = decision(context())
    second = decision(context())
    assert first.outcome == "allow"
    assert first.reasons == ()
    assert first.rule_set_version == "risk.v1"
    assert first.input_sha256 == second.input_sha256


def test_pretrade_chain_denies_every_specified_violation_with_explanations() -> None:
    check = context(
        actor_authorized=False,
        strategy_approved=False,
        runtime_active=False,
        market_open=False,
        instrument_enabled=False,
        planned_price=Decimal("12"),
        reference_price=Decimal("10"),
        orders_in_window=5,
        current_position_quantity=Decimal("9"),
        current_exposure=Decimal("999"),
        daily_loss=Decimal("100"),
        current_drawdown=Decimal("0.25"),
        kill_switch_active=True,
        existing_intent_ids=(intent().intent_id,),
    )
    result = decision(check, policy(quantity_step=Decimal("3")))
    codes = {reason.code for reason in result.reasons}
    assert result.outcome == "deny"
    assert codes == {
        "actor_unauthorized",
        "strategy_unapproved",
        "runtime_inactive",
        "market_closed",
        "instrument_disabled",
        "kill_switch_active",
        "price_deviation",
        "quantity_precision",
        "duplicate_intent",
        "order_frequency",
        "position_limit",
        "exposure_limit",
        "daily_loss_limit",
        "drawdown_limit",
    }
    assert all(reason.rule_version == "risk.v1" for reason in result.reasons)
    assert all(reason.summary for reason in result.reasons)


def test_approval_requirement_is_explained_and_input_hash_covers_policy() -> None:
    approval = decision(context(requires_approval=True))
    changed_policy = decision(context(), policy(version="risk.v2"))
    assert approval.outcome == "require_approval"
    assert approval.reasons[0].code == "approval_required"
    assert changed_policy.input_sha256 != decision(context()).input_sha256


def test_sell_that_closes_a_long_position_reduces_existing_exposure() -> None:
    sell_intent = intent().model_copy(update={"side": "sell"})
    result = decision(
        context(
            order_intent=sell_intent,
            current_position_quantity=Decimal("5"),
            current_exposure=Decimal("40"),
        ),
        policy(max_exposure=Decimal("50")),
    )
    assert result.outcome == "allow"


def test_layered_limits_calculate_usage_and_reject_each_breached_scope() -> None:
    limits = (
        RiskLimit(
            "account.exposure",
            "account",
            "account-1",
            "notional_exposure",
            Decimal("100"),
            "limits.v3",
        ),
        RiskLimit(
            "workspace.exposure",
            "workspace",
            "workspace-1",
            "notional_exposure",
            Decimal("200"),
            "limits.v8",
        ),
        RiskLimit(
            "instrument.position",
            "instrument",
            "MOCK:ABC",
            "position_quantity",
            Decimal("50"),
            "limits.v2",
        ),
    )
    projections = (
        LimitProjection("account", "account-1", "notional_exposure", Decimal("70"), Decimal("110")),
        LimitProjection(
            "workspace", "workspace-1", "notional_exposure", Decimal("70"), Decimal("110")
        ),
        LimitProjection(
            "instrument", "MOCK:ABC", "position_quantity", Decimal("10"), Decimal("60")
        ),
    )
    usage = calculate_limit_usage(limits, projections)
    assert [item.utilization for item in usage] == [
        Decimal("1.1"),
        Decimal("0.55"),
        Decimal("1.2"),
    ]
    assert [item.breached for item in usage] == [True, False, True]

    result = decision(context(limit_usages=usage))
    reordered = decision(context(limit_usages=tuple(reversed(usage))))

    assert result.outcome == "deny"
    assert result.input_sha256 == reordered.input_sha256
    assert result.reasons == reordered.reasons
    assert {reason.rule_id for reason in result.reasons} == {
        "account.exposure",
        "instrument.position",
    }
    assert {reason.rule_version for reason in result.reasons} == {"limits.v2", "limits.v3"}


def test_layered_limit_calculation_rejects_missing_projection() -> None:
    configured = (
        RiskLimit(
            "account.loss",
            "account",
            "account-1",
            "daily_loss",
            Decimal("100"),
            "limits.v1",
        ),
    )
    with pytest.raises(PortError) as error:
        calculate_limit_usage(configured, ())
    assert error.value.code == ErrorCode.INVALID_INPUT
