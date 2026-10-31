from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from qtrade_data_contracts.research import CorporateAction
from qtrade_domain.corporate_actions import CorporateActionLedger
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
        {
            "action": "rights",
            "cash": None,
            "cash_currency": None,
            "rights_shares": Decimal("1"),
            "rights_price": Decimal("1"),
        },
        {"ex_date": None},
    ],
)
def test_unmodeled_or_undated_actions_fail_closed(overrides):
    with pytest.raises(PortError) as error:
        CorporateActionLedger((action(**overrides),))
    assert error.value.code == ErrorCode.UNSUPPORTED
