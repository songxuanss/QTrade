from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from qtrade_application.indicators import IndicatorConfig, calculate_indicators
from qtrade_contracts import Bar


def make_bars(closes: list[int]) -> tuple[Bar, ...]:
    workspace_id, dataset_id, batch_id = uuid4(), uuid4(), uuid4()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        Bar(
            schema_version=1,
            workspace_id=workspace_id,
            instrument_id="TEST:ONE",
            specification_version=1,
            timestamp=start + timedelta(minutes=index),
            granularity="1m",
            open=Decimal(close),
            high=Decimal(close + 1),
            low=Decimal(close - 1),
            close=Decimal(close),
            volume=Decimal(100),
            batch_id=batch_id,
            dataset_id=dataset_id,
            quality="valid",
        )
        for index, close in enumerate(closes)
    )


def test_indicator_series_align_values_and_respect_initial_samples():
    series = calculate_indicators(make_bars(list(range(10, 50))))

    assert len(series.ma) == 40
    assert series.ma[18] is None
    assert series.ma[19] == Decimal("19.5")
    assert series.ema[18] is None
    assert series.ema[19] == Decimal("19.5")
    assert series.macd[24] is None
    assert series.macd[25] is not None
    assert series.macd_signal[32] is None
    assert series.macd_signal[33] is not None
    assert series.macd_histogram[33] is not None
    assert series.rsi[13] is None
    assert series.rsi[14] == Decimal(100)
    assert series.bollinger_middle[18] is None
    assert series.bollinger_middle[19] == Decimal("19.5")
    assert series.bollinger_upper[19] > series.bollinger_middle[19]
    assert series.bollinger_lower[19] < series.bollinger_middle[19]
    assert series.atr[12] is None
    assert series.atr[13] == Decimal(2)
    assert series.atr[14] == Decimal(2)


def test_insufficient_samples_remain_aligned_as_none():
    series = calculate_indicators(make_bars([10, 11, 10]))
    assert series.ma == (None, None, None)
    assert series.macd == (None, None, None)
    assert series.rsi == (None, None, None)
    assert series.bollinger_upper == (None, None, None)
    assert series.atr == (None, None, None)


@pytest.mark.parametrize(
    "config",
    [
        {"ma_period": 1},
        {"ema_period": 501},
        {"macd_fast": 26, "macd_slow": 26},
        {"bollinger_deviations": Decimal(0)},
    ],
)
def test_invalid_indicator_parameters_fail_at_configuration(config):
    with pytest.raises(ValueError):
        IndicatorConfig(**config)


def test_indicator_input_must_be_strictly_chronological():
    bars = make_bars([10, 11, 12])
    with pytest.raises(ValueError, match="strictly chronological"):
        calculate_indicators((bars[1], bars[0], bars[2]))
