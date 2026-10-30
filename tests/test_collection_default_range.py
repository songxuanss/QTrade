"""Default dates use market calendar/source release rules, never stored coverage."""

from datetime import UTC, date, datetime, time

import pytest

from qtrade_data_core.default_range import natural_year_range
from qtrade_data_core.ports import DataFailure


def calculate(now, days):
    return natural_year_range(
        now,
        days,
        timezone="Asia/Shanghai",
        release_time=time(17, 10),
        basis="fixture:documented close publication",
    )


def test_release_boundary_weekend_and_holiday():
    days = (date(2026, 9, 30), date(2026, 10, 9), date(2026, 10, 12))
    before = calculate(datetime(2026, 10, 12, 9, 9, tzinfo=UTC), days)
    after = calculate(datetime(2026, 10, 12, 9, 10, tzinfo=UTC), days)
    assert before.last == date(2026, 10, 9) and after.last == date(2026, 10, 12)
    assert calculate(datetime(2026, 10, 4, 12, tzinfo=UTC), days).last == date(2026, 9, 30)
    assert calculate(datetime(2026, 10, 11, 12, tzinfo=UTC), days).last == date(2026, 10, 9)
    assert after.first == date(2025, 10, 13)
    assert after.end.date() == date(2026, 10, 13)


def test_leap_calendar_year_and_missing_calendar():
    result = calculate(datetime(2024, 2, 28, 12, tzinfo=UTC), (date(2024, 2, 28),))
    assert result.end.date() == date(2024, 2, 29) and result.first == date(2023, 2, 28)
    with pytest.raises(DataFailure, match="unavailable"):
        calculate(datetime(2026, 1, 1, tzinfo=UTC), ())
    with pytest.raises(ValueError):
        calculate(datetime(2026, 1, 1), ())
