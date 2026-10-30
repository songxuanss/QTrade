from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrade_contracts import BacktestEquityPoint, BacktestFill, BacktestPosition
from qtrade_domain.backtest_metrics import calculate_backtest_metrics
from qtrade_domain.ports import PortError


def point(day: int, equity: str) -> BacktestEquityPoint:
    return BacktestEquityPoint(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day),
        cash=Decimal(equity),
        equity=Decimal(equity),
    )


def test_metrics_cover_returns_drawdowns_and_explicit_frequency():
    result = calculate_backtest_metrics(
        (point(0, "100"), point(1, "110"), point(2, "99")),
        periods_per_year=252,
        benchmark_return=Decimal("0.02"),
    )
    assert result.max_drawdown == Decimal("0.1")
    assert result.annualized_return is not None and result.annualized_return < 0
    assert result.annualized_volatility is not None and result.annualized_volatility > 0
    assert result.sharpe_ratio is not None
    assert result.sortino_ratio is not None
    assert result.benchmark_return == Decimal("0.02")


def test_metrics_leave_undefined_statistics_empty_for_small_or_flat_samples():
    single = calculate_backtest_metrics((point(0, "100"),), periods_per_year=252)
    assert single.annualized_return is None
    assert single.annualized_volatility is None
    assert single.sharpe_ratio is None
    flat = calculate_backtest_metrics((point(0, "100"), point(1, "100")), periods_per_year=252)
    assert flat.sharpe_ratio is None
    assert flat.annualized_volatility is None


def test_metrics_calculate_fifo_trade_quality_turnover_and_exposure():
    points = (
        BacktestEquityPoint(
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            cash=Decimal("100"),
            equity=Decimal("100"),
            positions=(
                BacktestPosition(
                    instrument_id="SSE:600000",
                    quantity=Decimal("10"),
                    market_value=Decimal("100"),
                ),
            ),
        ),
        point(1, "101"),
        point(2, "102"),
    )
    fills = (
        BacktestFill(
            instrument_id="SSE:600000",
            side="buy",
            filled_quantity=Decimal("10"),
            filled_at=points[0].timestamp,
            price=Decimal("10"),
            fee=Decimal("1"),
        ),
        BacktestFill(
            instrument_id="SSE:600000",
            side="sell",
            filled_quantity=Decimal("5"),
            filled_at=points[1].timestamp,
            price=Decimal("12"),
            fee=Decimal("1"),
        ),
        BacktestFill(
            instrument_id="SSE:600000",
            side="sell",
            filled_quantity=Decimal("5"),
            filled_at=points[2].timestamp,
            price=Decimal("9"),
            fee=Decimal("1"),
        ),
    )
    result = calculate_backtest_metrics(points, fills=fills, initial_cash=Decimal("100"))
    assert result.trade_count == 2
    assert result.winning_trades == result.losing_trades == 1
    assert result.win_rate == Decimal("0.5")
    assert result.profit_factor == Decimal("1.307692307692307692307692308")
    assert result.turnover == Decimal("2.05")
    assert result.max_exposure == Decimal("1")
    assert result.average_exposure == Decimal("0.3333333333333333333333333333")


@pytest.mark.parametrize("periods", [0, 1, True])
def test_metrics_reject_invalid_annualization_period(periods: int):
    with pytest.raises(PortError):
        calculate_backtest_metrics((point(0, "100"), point(1, "101")), periods_per_year=periods)
