"""Calendar and publication-rule based natural-year defaults, independent of local coverage."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from qtrade_data_core.ports import DataFailure


@dataclass(frozen=True)
class DefaultRange:
    first: date
    last: date
    start: datetime
    end: datetime
    timezone: str
    expected_available_at: datetime
    basis: str


def natural_year_range(
    now: datetime,
    trading_days: tuple[date, ...],
    *,
    timezone: str,
    release_time: time,
    basis: str,
) -> DefaultRange:
    if now.tzinfo is None or release_time.tzinfo is not None or not basis:
        raise ValueError("explicit timezone and release evidence required")
    zone = ZoneInfo(timezone)
    eligible = [day for day in trading_days if datetime.combine(day, release_time, zone) <= now]
    if not eligible:
        raise DataFailure("unavailable")
    last = max(eligible)
    end_day = last + timedelta(days=1)
    # Calendar year subtraction, including leap-day clamping, not 365-day arithmetic.
    try:
        first = end_day.replace(year=end_day.year - 1)
    except ValueError:
        first = end_day.replace(year=end_day.year - 1, day=28)
    return DefaultRange(
        first,
        last,
        datetime.combine(first, time.min, zone),
        datetime.combine(end_day, time.min, zone),
        timezone,
        datetime.combine(last, release_time, zone),
        basis,
    )
