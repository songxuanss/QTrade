"""Deterministic market execution rules shared by backtest engines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True, slots=True)
class BacktestExecutionModel:
    """Versioned, explicit assumptions for translating intents into fills.

    Times are local exchange wall-clock times. Prices are Decimal and fees/slippage
    are basis points. Defaults preserve generic continuous-market behavior.
    """

    lot_size: int = 1
    fee_bps: Decimal = Decimal(0)
    minimum_fee: Decimal = Decimal(0)
    sell_tax_bps: Decimal = Decimal(0)
    slippage_bps: Decimal = Decimal(0)
    tick_size: Decimal = Decimal(0)
    settlement_days: int = 0
    max_volume_fraction: Decimal = Decimal(1)
    latency_bars: int = 1
    session_open: time | None = None
    session_close: time | None = None
    session_breaks: tuple[tuple[time, time], ...] = ()
    daily_price_limit_fraction: Decimal | None = None
    timezone_name: str = "UTC"
    periods_per_year: int | None = None

    @classmethod
    def from_config(cls, config: dict[str, object] | None) -> BacktestExecutionModel:
        if config is None:
            return cls()
        if not isinstance(config, dict):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid execution model configuration")
        allowed = {
            "lot_size",
            "fee_bps",
            "minimum_fee",
            "sell_tax_bps",
            "slippage_bps",
            "tick_size",
            "settlement_days",
            "max_volume_fraction",
            "latency_bars",
            "session_open",
            "session_close",
            "session_breaks",
            "daily_price_limit_fraction",
            "timezone_name",
            "periods_per_year",
        }
        if any(key not in allowed for key in config):
            raise PortError(ErrorCode.INVALID_INPUT, "unknown execution model setting")

        def decimal_value(key: str, default: str) -> Decimal:
            try:
                value = Decimal(str(config.get(key, default)))
            except (ValueError, ArithmeticError):
                raise PortError(
                    ErrorCode.INVALID_INPUT, "invalid execution model setting"
                ) from None
            if not value.is_finite():
                raise PortError(ErrorCode.INVALID_INPUT, "invalid execution model setting")
            return value

        def session_time(key: str) -> time | None:
            value = config.get(key)
            if value is None:
                return None
            if not isinstance(value, str):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid execution session time")
            try:
                return time.fromisoformat(value)
            except ValueError:
                raise PortError(ErrorCode.INVALID_INPUT, "invalid execution session time") from None

        raw_breaks = config.get("session_breaks", [])
        if not isinstance(raw_breaks, list):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid execution session breaks")
        breaks: list[tuple[time, time]] = []
        for interval in raw_breaks:
            if (
                not isinstance(interval, list)
                or len(interval) != 2
                or any(not isinstance(value, str) for value in interval)
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid execution session breaks")
            try:
                breaks.append((time.fromisoformat(interval[0]), time.fromisoformat(interval[1])))
            except ValueError:
                raise PortError(
                    ErrorCode.INVALID_INPUT, "invalid execution session breaks"
                ) from None

        try:
            return cls(
                lot_size=config.get("lot_size", 1),  # type: ignore[arg-type]
                fee_bps=decimal_value("fee_bps", "0"),
                minimum_fee=decimal_value("minimum_fee", "0"),
                sell_tax_bps=decimal_value("sell_tax_bps", "0"),
                slippage_bps=decimal_value("slippage_bps", "0"),
                tick_size=decimal_value("tick_size", "0"),
                settlement_days=config.get("settlement_days", 0),  # type: ignore[arg-type]
                max_volume_fraction=decimal_value("max_volume_fraction", "1"),
                latency_bars=config.get("latency_bars", 1),  # type: ignore[arg-type]
                session_open=session_time("session_open"),
                session_close=session_time("session_close"),
                session_breaks=tuple(breaks),
                daily_price_limit_fraction=(
                    decimal_value("daily_price_limit_fraction", "0.1")
                    if config.get("daily_price_limit_fraction") is not None
                    else None
                ),
                timezone_name=config.get("timezone_name", "UTC"),  # type: ignore[arg-type]
                periods_per_year=config.get("periods_per_year"),  # type: ignore[arg-type]
            )
        except TypeError:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid execution model setting") from None

    def to_config(self) -> dict[str, object]:
        return {
            "daily_price_limit_fraction": (
                format(self.daily_price_limit_fraction, "f")
                if self.daily_price_limit_fraction is not None
                else None
            ),
            "fee_bps": format(self.fee_bps, "f"),
            "minimum_fee": format(self.minimum_fee, "f"),
            "sell_tax_bps": format(self.sell_tax_bps, "f"),
            "settlement_days": self.settlement_days,
            "tick_size": format(self.tick_size, "f"),
            "latency_bars": self.latency_bars,
            "lot_size": self.lot_size,
            "max_volume_fraction": format(self.max_volume_fraction, "f"),
            "periods_per_year": self.periods_per_year,
            "session_close": self.session_close.isoformat() if self.session_close else None,
            "session_breaks": [
                [opening.isoformat(), closing.isoformat()]
                for opening, closing in self.session_breaks
            ],
            "session_open": self.session_open.isoformat() if self.session_open else None,
            "slippage_bps": format(self.slippage_bps, "f"),
            "timezone_name": self.timezone_name,
        }

    def __post_init__(self) -> None:
        if (
            not isinstance(self.session_breaks, tuple)
            or any(
                not isinstance(interval, tuple)
                or len(interval) != 2
                or any(not isinstance(value, time) for value in interval)
                for interval in self.session_breaks
            )
            or any(
                value is not None and not isinstance(value, time)
                for value in (self.session_open, self.session_close)
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest session configuration")
        rates = (
            self.fee_bps,
            self.minimum_fee,
            self.sell_tax_bps,
            self.slippage_bps,
            self.tick_size,
            self.max_volume_fraction,
        )
        if (
            type(self.lot_size) is not int
            or self.lot_size < 1
            or self.lot_size > 1_000_000_000
            or type(self.latency_bars) is not int
            or self.latency_bars < 1
            or self.latency_bars > 100_000
            or any(
                not isinstance(value, Decimal) or not value.is_finite() or value < 0
                for value in rates
            )
            or self.max_volume_fraction > 1
            or self.fee_bps >= Decimal(10000)
            or self.sell_tax_bps >= Decimal(10000)
            or self.slippage_bps >= Decimal(10000)
            or self.tick_size >= Decimal(1000)
            or type(self.settlement_days) is not int
            or not 0 <= self.settlement_days <= 1
            or (
                self.daily_price_limit_fraction is not None
                and (
                    not isinstance(self.daily_price_limit_fraction, Decimal)
                    or not self.daily_price_limit_fraction.is_finite()
                    or not Decimal(0) < self.daily_price_limit_fraction < Decimal(1)
                )
            )
            or ((self.session_open is None) != (self.session_close is None))
            or any(
                session is not None and session.utcoffset() is not None
                for session in (self.session_open, self.session_close)
            )
            or (
                self.session_open is not None
                and self.session_close is not None
                and self.session_open >= self.session_close
            )
            or not isinstance(self.timezone_name, str)
            or not self.timezone_name
            or (
                self.periods_per_year is not None
                and (
                    type(self.periods_per_year) is not int
                    or not 2 <= self.periods_per_year <= 1_000_000
                )
            )
            or any(
                opening >= closing
                or opening.utcoffset() is not None
                or closing.utcoffset() is not None
                or (self.session_open is not None and opening < self.session_open)
                or (self.session_close is not None and closing > self.session_close)
                for opening, closing in self.session_breaks
            )
            or any(
                left[1] > right[0]
                for left, right in zip(self.session_breaks, self.session_breaks[1:], strict=False)
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest execution model")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            raise PortError(ErrorCode.INVALID_INPUT, "unknown execution timezone") from None

    def round_quantity(self, quantity: Decimal) -> Decimal:
        return (quantity / self.lot_size).to_integral_value(rounding=ROUND_DOWN) * self.lot_size

    def volume_capacity(self, volume: Decimal) -> Decimal:
        return (volume * self.max_volume_fraction).to_integral_value(rounding=ROUND_DOWN)

    def in_session(self, local_time: time) -> bool:
        within_window = (
            self.session_open is None
            or self.session_close is None
            or self.session_open <= local_time < self.session_close
        )
        return within_window and not any(
            opening <= local_time < closing for opening, closing in self.session_breaks
        )

    def fill_price(self, side: str, reference: Decimal) -> Decimal:
        if side not in {"buy", "sell"}:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid execution side")
        direction = Decimal(1) if side == "buy" else Decimal(-1)
        price = reference * (Decimal(1) + direction * self.slippage_bps / Decimal(10000))
        if self.tick_size > 0:
            rounding = ROUND_CEILING if side == "buy" else ROUND_FLOOR
            price = (price / self.tick_size).to_integral_value(rounding=rounding) * self.tick_size
        return price

    def fee(self, notional: Decimal, side: str = "buy") -> Decimal:
        if side not in {"buy", "sell"}:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid execution side")
        commission = notional * self.fee_bps / Decimal(10000)
        if commission > 0 and self.minimum_fee > commission:
            commission = self.minimum_fee
        return commission + (
            notional * self.sell_tax_bps / Decimal(10000) if side == "sell" else Decimal(0)
        )

    def within_daily_limit(self, price: Decimal, previous_close: Decimal | None) -> bool:
        if self.daily_price_limit_fraction is None or previous_close is None:
            return True
        fraction = self.daily_price_limit_fraction
        return previous_close * (1 - fraction) <= price <= previous_close * (1 + fraction)
