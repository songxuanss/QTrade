from datetime import time
from decimal import Decimal

import pytest

from qtrade_domain.backtest_execution import BacktestExecutionModel
from qtrade_domain.ports import PortError


def test_execution_model_applies_a_share_lot_fee_slippage_and_participation() -> None:
    model = BacktestExecutionModel(
        lot_size=100,
        fee_bps=Decimal("3"),
        slippage_bps=Decimal("10"),
        max_volume_fraction=Decimal("0.25"),
        session_open=time(9, 30),
        session_close=time(15),
        session_breaks=((time(11, 30), time(13)),),
        daily_price_limit_fraction=Decimal("0.1"),
    )
    assert model.round_quantity(Decimal("250")) == Decimal("200")
    assert model.fill_price("buy", Decimal("10")) == Decimal("10.010")
    assert model.fee(Decimal("1001")) == Decimal("0.3003")
    assert model.in_session(time(9, 30))
    assert not model.in_session(time(15))
    assert not model.in_session(time(12))
    assert model.in_session(time(13))
    assert model.within_daily_limit(Decimal("11"), Decimal("10"))
    assert not model.within_daily_limit(Decimal("11.01"), Decimal("10"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lot_size": 0},
        {"lot_size": 1_000_000_001},
        {"latency_bars": 0},
        {"fee_bps": Decimal("-1")},
        {"max_volume_fraction": Decimal("1.1")},
        {"session_open": time(15), "session_close": time(9, 30)},
        {
            "session_open": time(9, 30),
            "session_close": time(15),
            "session_breaks": ((time(11), time(13)), (time(12), time(14))),
        },
        {"daily_price_limit_fraction": Decimal("1")},
        {"timezone_name": "Mars/Olympus"},
    ],
)
def test_execution_model_rejects_invalid_assumptions(kwargs: dict[str, object]) -> None:
    with pytest.raises(PortError):
        BacktestExecutionModel(**kwargs)  # type: ignore[arg-type]


def test_tick_size_minimum_fee_and_sell_tax_are_applied():
    model = BacktestExecutionModel(
        tick_size=Decimal("0.01"),
        fee_bps=Decimal("3"),
        minimum_fee=Decimal("5"),
        sell_tax_bps=Decimal("5"),
    )
    assert model.fill_price("buy", Decimal("10.005")) == Decimal("10.01")
    assert model.fill_price("sell", Decimal("10.005")) == Decimal("10.00")
    assert model.fee(Decimal("1000"), "buy") == Decimal("5")
    assert model.fee(Decimal("100000"), "sell") == Decimal("80")
