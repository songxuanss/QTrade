"""Deterministic technical indicators over an ordered OHLCV sample."""

from dataclasses import dataclass
from decimal import Decimal
from math import sqrt

from qtrade_contracts import Bar


@dataclass(frozen=True, slots=True)
class IndicatorConfig:
    ma_period: int = 20
    ema_period: int = 20
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    rsi_period: int = 14
    bollinger_period: int = 20
    bollinger_deviations: Decimal = Decimal("2")
    atr_period: int = 14

    def __post_init__(self) -> None:
        periods = (
            self.ma_period,
            self.ema_period,
            self.macd_fast,
            self.macd_slow,
            self.macd_signal,
            self.rsi_period,
            self.bollinger_period,
            self.atr_period,
        )
        if any(type(period) is not int or not 2 <= period <= 500 for period in periods):
            raise ValueError("indicator periods must be integers between 2 and 500")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("MACD fast period must be less than slow period")
        if not self.bollinger_deviations.is_finite() or self.bollinger_deviations <= 0:
            raise ValueError("Bollinger deviations must be positive and finite")


IndicatorValue = Decimal | None


@dataclass(frozen=True, slots=True)
class IndicatorSeries:
    ma: tuple[IndicatorValue, ...]
    ema: tuple[IndicatorValue, ...]
    macd: tuple[IndicatorValue, ...]
    macd_signal: tuple[IndicatorValue, ...]
    macd_histogram: tuple[IndicatorValue, ...]
    rsi: tuple[IndicatorValue, ...]
    bollinger_upper: tuple[IndicatorValue, ...]
    bollinger_middle: tuple[IndicatorValue, ...]
    bollinger_lower: tuple[IndicatorValue, ...]
    atr: tuple[IndicatorValue, ...]


def _ema(values: tuple[Decimal, ...], period: int) -> tuple[IndicatorValue, ...]:
    result: list[IndicatorValue] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    seed = sum(values[:period], Decimal(0)) / period
    result[period - 1] = seed
    alpha = Decimal(2) / (period + 1)
    previous = seed
    for index in range(period, len(values)):
        previous += alpha * (values[index] - previous)
        result[index] = previous
    return tuple(result)


def calculate_indicators(
    bars: tuple[Bar, ...], config: IndicatorConfig | None = None
) -> IndicatorSeries:
    """Return bar-aligned MA, EMA, MACD, RSI, Bollinger and ATR values.

    Insufficient history is represented by ``None`` at the affected indexes.
    Bars must be strictly chronological, which protects consumers from silently
    displaying mathematically valid but time-misaligned results.
    """
    config = config or IndicatorConfig()
    if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:], strict=False)):
        raise ValueError("indicator bars must be strictly chronological")
    closes = tuple(bar.close for bar in bars)
    length = len(bars)

    ma: list[IndicatorValue] = [None] * length
    for index in range(config.ma_period - 1, length):
        ma[index] = (
            sum(closes[index - config.ma_period + 1 : index + 1], Decimal(0)) / config.ma_period
        )

    ema = _ema(closes, config.ema_period)
    fast = _ema(closes, config.macd_fast)
    slow = _ema(closes, config.macd_slow)
    macd: list[IndicatorValue] = []
    for index in range(length):
        fast_value, slow_value = fast[index], slow[index]
        macd.append(
            fast_value - slow_value if fast_value is not None and slow_value is not None else None
        )
    macd_start = config.macd_slow - 1
    signal_values = tuple(value for value in macd[macd_start:] if value is not None)
    signal_tail = _ema(signal_values, config.macd_signal)
    signal: list[IndicatorValue] = [None] * length
    for offset, value in enumerate(signal_tail):
        signal[macd_start + offset] = value
    histogram_values: list[IndicatorValue] = []
    for index, value in enumerate(macd):
        signal_value = signal[index]
        histogram_values.append(
            value - signal_value if value is not None and signal_value is not None else None
        )
    histogram = tuple(histogram_values)

    rsi: list[IndicatorValue] = [None] * length
    if length > config.rsi_period:
        changes = [closes[i] - closes[i - 1] for i in range(1, length)]
        gains = [max(change, Decimal(0)) for change in changes]
        losses = [max(-change, Decimal(0)) for change in changes]
        average_gain = sum(gains[: config.rsi_period], Decimal(0)) / config.rsi_period
        average_loss = sum(losses[: config.rsi_period], Decimal(0)) / config.rsi_period

        def rsi_value(gain: Decimal, loss: Decimal) -> Decimal:
            if loss == 0:
                return Decimal(100) if gain > 0 else Decimal(50)
            relative_strength = gain / loss
            return Decimal(100) - Decimal(100) / (Decimal(1) + relative_strength)

        first = config.rsi_period
        rsi[first] = rsi_value(average_gain, average_loss)
        for index in range(first + 1, length):
            change_index = index - 1
            average_gain = (
                average_gain * (config.rsi_period - 1) + gains[change_index]
            ) / config.rsi_period
            average_loss = (
                average_loss * (config.rsi_period - 1) + losses[change_index]
            ) / config.rsi_period
            rsi[index] = rsi_value(average_gain, average_loss)

    upper: list[IndicatorValue] = [None] * length
    middle: list[IndicatorValue] = [None] * length
    lower: list[IndicatorValue] = [None] * length
    for index in range(config.bollinger_period - 1, length):
        window = closes[index - config.bollinger_period + 1 : index + 1]
        average = sum(window, Decimal(0)) / config.bollinger_period
        variance = (
            sum(((value - average) ** 2 for value in window), Decimal(0)) / config.bollinger_period
        )
        deviation = Decimal(str(sqrt(float(variance))))
        middle[index] = average
        upper[index] = average + config.bollinger_deviations * deviation
        lower[index] = average - config.bollinger_deviations * deviation

    true_ranges: list[Decimal] = []
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1].close if index else bar.close
        true_ranges.append(
            max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))
        )
    atr: list[IndicatorValue] = [None] * length
    if length >= config.atr_period:
        previous_atr = sum(true_ranges[: config.atr_period], Decimal(0)) / config.atr_period
        atr[config.atr_period - 1] = previous_atr
        for index in range(config.atr_period, length):
            previous_atr = (
                previous_atr * (config.atr_period - 1) + true_ranges[index]
            ) / config.atr_period
            atr[index] = previous_atr

    return IndicatorSeries(
        tuple(ma),
        ema,
        tuple(macd),
        tuple(signal),
        histogram,
        tuple(rsi),
        tuple(upper),
        tuple(middle),
        tuple(lower),
        tuple(atr),
    )
