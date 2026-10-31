from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from qtrade_data_contracts.research import AdjustmentFactor, CorporateAction
from qtrade_domain.corporate_actions import CorporateActionLedger, RightsSubscription
from qtrade_domain.ports import ErrorCode, PortError


def action(**overrides):
    values = dict(
        schema_version=2,
        scope_id=uuid4(),
        source="mairui.research",
        instrument_id="600519.SH",
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        mapping_version="mairui.research.v1",
        source_event_id=f"event-{uuid4()}",
        action="distribution",
        announcement_date=date(2024, 12, 1),
        record_date=date(2024, 12, 20),
        ex_date=date(2024, 12, 23),
        payment_date=None,
        listing_date=None,
        status="implemented",
        cash=Decimal("5"),
        cash_currency="CNY",
        cash_tax_basis="before_tax",
        basis_shares=Decimal("10"),
        bonus_shares=None,
        transfer_shares=None,
        terms_evidence="fixture",
    )
    values.update(overrides)
    return CorporateAction.model_validate(values | {"kind": "corporate_action"})


def test_cash_dividend_and_bonus_shares_apply_on_ex_date_without_same_day_sellability():
    cash = action(source_event_id="cash")
    bonus = action(
        source_event_id="bonus", cash=None, cash_currency=None, bonus_shares=Decimal("3")
    )
    ledger = CorporateActionLedger((cash, bonus))
    before = ledger.apply(
        "600519.SH", date(2024, 12, 20), Decimal("100"), Decimal("100"), Decimal("1000"), "CNY"
    )
    assert (before.quantity, before.available, before.cash) == (
        Decimal("100"),
        Decimal("100"),
        Decimal("1000"),
    )
    result = ledger.apply(
        "600519.SH", date(2024, 12, 23), Decimal("100"), Decimal("100"), Decimal("1000"), "CNY"
    )
    assert result.quantity == Decimal("130")
    assert result.available == Decimal("100")
    assert result.cash == Decimal("1050")
    again = ledger.apply(
        "600519.SH", date(2024, 12, 23), Decimal("130"), Decimal("100"), Decimal("1050"), "CNY"
    )
    assert again.quantity == Decimal("130")
    assert again.cash == Decimal("1050")


def test_split_scales_position_and_sellable_quantity():
    ledger = CorporateActionLedger(
        (
            action(
                source_event_id="split",
                action="split",
                cash=None,
                cash_currency=None,
                split_ratio=Decimal("2"),
                bonus_shares=None,
            ),
        )
    )
    result = ledger.apply(
        "600519.SH", date(2024, 12, 23), Decimal("100"), Decimal("100"), Decimal("1000"), "CNY"
    )
    assert (result.quantity, result.available, result.cash) == (
        Decimal("200"),
        Decimal("200"),
        Decimal("1000"),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"ex_date": None},
    ],
)
def test_unmodeled_or_undated_actions_fail_closed(overrides):
    with pytest.raises(PortError) as error:
        CorporateActionLedger((action(**overrides),))
    assert error.value.code == ErrorCode.UNSUPPORTED


def rights(**overrides):
    values = dict(
        action="rights",
        cash=None,
        cash_currency=None,
        bonus_shares=None,
        transfer_shares=None,
        rights_shares=Decimal("3"),
        rights_price=Decimal("5"),
    )
    values.update(overrides)
    return action(**values)


def test_rights_subscription_spends_cash_and_adds_unsellable_shares():
    ledger = CorporateActionLedger((rights(source_event_id="rights"),))
    result = ledger.apply(
        "600519.SH",
        date(2024, 12, 23),
        Decimal("100"),
        Decimal("100"),
        Decimal("1000"),
        "CNY",
    )
    assert (result.quantity, result.available, result.cash) == (
        Decimal("130"),
        Decimal("100"),
        Decimal("850"),
    )
    assert result.subscriptions == (RightsSubscription("600519.SH", Decimal("30"), Decimal("5")),)
    again = ledger.apply(
        "600519.SH",
        date(2024, 12, 23),
        result.quantity,
        result.available,
        result.cash,
        "CNY",
    )
    assert again.subscriptions == ()
    assert again.cash == Decimal("850")


def test_rights_subscription_is_capped_by_available_cash():
    ledger = CorporateActionLedger((rights(source_event_id="rights"),))
    result = ledger.apply(
        "600519.SH",
        date(2024, 12, 23),
        Decimal("100"),
        Decimal("100"),
        Decimal("12"),
        "CNY",
    )
    assert result.quantity == Decimal("102")
    assert result.cash == Decimal("2")
    assert result.subscriptions[0].quantity == Decimal("2")


def test_rights_can_be_explicitly_skipped_or_rejected():
    skipped = CorporateActionLedger((rights(source_event_id="rights"),), "skip")
    result = skipped.apply(
        "600519.SH",
        date(2024, 12, 23),
        Decimal("100"),
        Decimal("100"),
        Decimal("1000"),
        "CNY",
    )
    assert (result.quantity, result.cash, result.subscriptions) == (
        Decimal("100"),
        Decimal("1000"),
        (),
    )
    with pytest.raises(PortError) as invalid:
        CorporateActionLedger((), "ignore")
    assert invalid.value.code == ErrorCode.INVALID_INPUT


def factor(observation: date, value: str, **overrides):
    values = dict(
        schema_version=2,
        scope_id=uuid4(),
        source="vendor.adjustment",
        instrument_id="600519.SH",
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        mapping_version="vendor.v1",
        observation_date=observation,
        adjustment="backward",
        factor=Decimal(value),
        base_date=date(2020, 1, 1),
        base_value=Decimal("1"),
        algorithm_version="vendor.v1",
        convention_evidence="fixture",
    )
    values.update(overrides)
    return AdjustmentFactor.model_validate(values)


def test_factor_changes_must_match_frozen_corporate_actions():
    from qtrade_domain.corporate_actions import validate_adjustment_factors

    events = (
        action(
            source_event_id="split",
            action="split",
            cash=None,
            cash_currency=None,
            bonus_shares=None,
            split_ratio=Decimal("2"),
        ),
    )
    factors = (
        factor(date(2024, 12, 20), "1"),
        factor(date(2024, 12, 23), "2"),
    )
    assert validate_adjustment_factors(events, factors, ("600519.SH",)) == factors

    with pytest.raises(PortError) as unexplained:
        validate_adjustment_factors((), factors, ("600519.SH",))
    assert unexplained.value.code == ErrorCode.UNSUPPORTED
    assert "2024-12-23" in str(unexplained.value)


def test_factor_conventions_and_scope_are_enforced():
    from qtrade_domain.corporate_actions import validate_adjustment_factors

    mixed = (
        factor(date(2024, 12, 20), "1"),
        factor(date(2024, 12, 23), "2", adjustment="forward"),
    )
    with pytest.raises(PortError) as ambiguous:
        validate_adjustment_factors((), mixed, ("600519.SH",))
    assert ambiguous.value.code == ErrorCode.UNSUPPORTED

    outside = (factor(date(2024, 12, 20), "1", instrument_id="600036.SH"),)
    with pytest.raises(PortError) as scope:
        validate_adjustment_factors((), outside, ("600519.SH",))
    assert scope.value.code == ErrorCode.INVALID_INPUT
