"""Calendar/publication eligibility, with controlled calendar evidence only."""

from datetime import date, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from test_collection_schedules import draft

from qtrade_data_contracts.schedules import MarketScheduleSelection
from qtrade_data_core.default_range import natural_year_range
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.schedule_policy import DailyPriceSchedulePolicy

ZONE = ZoneInfo("Asia/Shanghai")


class PublicationFixture:
    def __init__(self):
        self.calls = []

    def default_range(self, source, granularity, now):
        self.calls.append((source, granularity, now))
        return natural_year_range(
            now,
            (date(2026, 10, 9), date(2026, 10, 12)),
            timezone="Asia/Shanghai",
            release_time=time(17, 10),
            basis="fixture publication estimate",
        )


def test_daily_release_has_half_open_lookback_and_holidays_skip_normally():
    provider = PublicationFixture()
    policy = DailyPriceSchedulePolicy(provider)
    value = draft(lookback_days=7)
    due = datetime(2026, 10, 9, 17, 15, tzinfo=ZONE)
    frozen = policy.prepare(value, uuid4(), due, due)
    assert frozen.dispatch.request.start.astimezone(ZONE) == datetime(2026, 10, 3, tzinfo=ZONE)
    assert frozen.dispatch.request.end.astimezone(ZONE) == datetime(2026, 10, 10, tzinfo=ZONE)
    assert frozen.dispatch.request.mode == "missing"
    for day in (10, 11, 13):  # Fixture calendar deliberately has no publication on 13.
        instant = datetime(2026, 10, day, 17, 15, tzinfo=ZONE)
        assert policy.prepare(value, uuid4(), instant, instant) is None
    monday = datetime(2026, 10, 12, 17, 15, tzinfo=ZONE)
    assert policy.prepare(value, uuid4(), monday, monday) is not None


def test_pre_release_cadence_collects_prior_day_only_when_newly_available():
    policy = DailyPriceSchedulePolicy(PublicationFixture())
    value = draft(local_time=time(17, 0))
    monday = datetime(2026, 10, 12, 17, 0, tzinfo=ZONE)
    assert policy.prepare(value, uuid4(), monday, monday) is None
    tuesday = datetime(2026, 10, 13, 17, 0, tzinfo=ZONE)
    frozen = policy.prepare(value, uuid4(), tuesday, tuesday)
    assert frozen.dispatch.request.end.astimezone(ZONE).date() == date(2026, 10, 13)


def test_weekly_operator_slot_can_collect_friday_on_sunday():
    policy = DailyPriceSchedulePolicy(PublicationFixture())
    value = draft(cadence="weekly", weekday=6)
    sunday = datetime(2026, 10, 11, 17, 15, tzinfo=ZONE)
    assert policy.prepare(value, uuid4(), sunday, sunday) is not None


def test_factor_frequency_is_not_inferred_from_price_calendar():
    provider = PublicationFixture()
    value = draft(
        selection=MarketScheduleSelection(
            kind="market",
            source="mairui.factor.history",
            dataset_kind="factors",
            granularity="1d",
            fields=("pe",),
            instrument_ids=("601288.SH",),
        )
    )
    due = datetime(2026, 10, 12, 17, 15, tzinfo=ZONE)
    with pytest.raises(DataFailure, match="unsupported"):
        DailyPriceSchedulePolicy(provider).prepare(value, uuid4(), due, due)
    assert provider.calls == []
