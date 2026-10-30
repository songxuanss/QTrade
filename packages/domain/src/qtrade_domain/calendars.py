"""Versioned session schedules with explicit closures and fail-closed DST handling."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class TradingSession:
    opens: time
    closes: time
    close_day_offset: int = 0
    open_fold: int | None = None
    close_fold: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.opens, time)
            or not isinstance(self.closes, time)
            or self.opens.tzinfo is not None
            or self.closes.tzinfo is not None
            or type(self.close_day_offset) is not int
            or self.close_day_offset not in (0, 1)
            or any(
                value is not None and (type(value) is not int or value not in (0, 1))
                for value in (self.open_fold, self.close_fold)
            )
        ):
            raise ValueError("invalid trading session")


@dataclass(frozen=True)
class DayOverride:
    trading_date: date
    sessions: tuple[TradingSession, ...]


@dataclass(frozen=True)
class SessionWindow:
    trading_date: date
    opens_at: datetime
    closes_at: datetime


def local_instant(day: date, clock: time, timezone: ZoneInfo, fold: int | None) -> datetime:
    naive = datetime.combine(day, clock)
    candidates = {
        candidate.astimezone(UTC)
        for choice in (0, 1)
        if (candidate := naive.replace(tzinfo=timezone, fold=choice))
        .astimezone(UTC)
        .astimezone(timezone)
        .replace(tzinfo=None)
        == naive
    }
    if not candidates:
        raise PortError(ErrorCode.INVALID_INPUT, "session boundary is a nonexistent local time")
    if len(candidates) > 1:
        if fold is None:
            raise PortError(ErrorCode.INVALID_INPUT, "ambiguous session boundary requires fold")
        return naive.replace(tzinfo=timezone, fold=fold).astimezone(UTC)
    return next(iter(candidates))


@dataclass(frozen=True)
class TradingCalendar:
    calendar_id: str
    version: int
    timezone: str
    effective_from: date
    effective_to: date
    weekdays: frozenset[int]
    sessions: tuple[TradingSession, ...]
    overrides: tuple[DayOverride, ...] = ()

    def __post_init__(self) -> None:
        ZoneInfo(self.timezone)
        if (
            not 1 <= len(self.calendar_id) <= 256
            or type(self.version) is not int
            or self.version < 1
            or type(self.effective_from) is not date
            or type(self.effective_to) is not date
            or self.effective_from > self.effective_to
            or not isinstance(self.weekdays, frozenset)
            or any(type(day) is not int or not 0 <= day <= 6 for day in self.weekdays)
            or not isinstance(self.sessions, tuple)
            or not isinstance(self.overrides, tuple)
            or any(not isinstance(session, TradingSession) for session in self.sessions)
            or any(
                not isinstance(override, DayOverride)
                or not isinstance(override.sessions, tuple)
                or any(not isinstance(session, TradingSession) for session in override.sessions)
                or type(override.trading_date) is not date
                or not self.effective_from <= override.trading_date <= self.effective_to
                for override in self.overrides
            )
            or len({override.trading_date for override in self.overrides}) != len(self.overrides)
        ):
            raise ValueError("invalid trading calendar")

    def windows(self, trading_date: date) -> tuple[SessionWindow, ...]:
        if type(trading_date) is not date:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid trading date")
        if not self.effective_from <= trading_date <= self.effective_to:
            raise PortError(ErrorCode.UNAVAILABLE, "calendar date outside declared coverage")
        override = next(
            (item for item in self.overrides if item.trading_date == trading_date), None
        )
        sessions = (
            override.sessions
            if override is not None
            else (self.sessions if trading_date.weekday() in self.weekdays else ())
        )
        timezone = ZoneInfo(self.timezone)
        result = tuple(
            sorted(
                (
                    SessionWindow(
                        trading_date,
                        local_instant(trading_date, session.opens, timezone, session.open_fold),
                        local_instant(
                            trading_date + timedelta(days=session.close_day_offset),
                            session.closes,
                            timezone,
                            session.close_fold,
                        ),
                    )
                    for session in sessions
                ),
                key=lambda window: window.opens_at,
            )
        )
        if any(window.opens_at >= window.closes_at for window in result) or any(
            previous.closes_at > following.opens_at
            for previous, following in zip(result, result[1:], strict=False)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid or overlapping trading sessions")
        return result

    def active_window(self, instant: datetime) -> SessionWindow | None:
        if instant.utcoffset() is None:
            raise PortError(ErrorCode.INVALID_INPUT, "market state requires an aware timestamp")
        current = instant.astimezone(UTC)
        local_date = current.astimezone(ZoneInfo(self.timezone)).date()
        today = self.windows(local_date)
        previous_date = local_date - timedelta(days=1)
        previous = self.windows(previous_date) if previous_date >= self.effective_from else ()
        active = tuple(
            window
            for window in (*previous, *today)
            if window.opens_at <= current < window.closes_at
        )
        if len(active) > 1:
            raise PortError(ErrorCode.INVALID_INPUT, "overlapping adjacent-day trading sessions")
        return active[0] if active else None


@dataclass(frozen=True)
class CalendarSnapshot:
    workspace_id: UUID
    calendar: TradingCalendar


class CalendarStorePort(Protocol):
    def put(self, actor_id: UUID, snapshot: CalendarSnapshot) -> None: ...
    def get(
        self, workspace_id: UUID, calendar_id: str, version: int | None = None
    ) -> CalendarSnapshot: ...
