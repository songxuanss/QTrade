"""Cadence, bounded recovery, timezone equivalence and DST slot identity."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_collection_schedules import draft

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.schedule_clock import due_slots, next_slot


def test_daily_inclusive_recovery_exact_boundary_and_utc_identity():
    value = draft()
    due = datetime(2026, 10, 14, 9, 15, tzinfo=UTC)
    before = due_slots(value, due, due - timedelta(seconds=1))
    assert before.slots == () and before.next_due == due
    current = due_slots(value, due, due)
    assert current.slots == (due,) and current.next_due == due + timedelta(days=1)
    assert due_slots(value, due.astimezone(ZoneInfo("Asia/Shanghai")), due) == current


def test_long_outage_never_enumerates_old_slots():
    value = draft(catchup_days=7)
    now = datetime(2026, 10, 14, 10, tzinfo=UTC)
    result = due_slots(value, datetime(2000, 1, 1, tzinfo=UTC), now)
    assert len(result.slots) == 7
    assert all(now - timedelta(days=7) <= slot <= now for slot in result.slots)
    assert result.omitted_before == now - timedelta(days=7)
    assert result.next_due > now


def test_weekly_and_quarterly_are_operator_cadences_not_market_calendars():
    weekly = draft(cadence="weekly", weekday=6, catchup_days=31)
    now = datetime(2026, 10, 14, 10, tzinfo=UTC)
    result = due_slots(weekly, now - timedelta(days=31), now)
    assert len(result.slots) == 4 and all(slot.weekday() == 6 for slot in result.slots)
    quarter = draft(cadence="quarterly", catchup_days=31)
    result = due_slots(quarter, datetime(2026, 9, 15, tzinfo=UTC), now)
    assert result.slots == (datetime(2026, 10, 1, 9, 15, tzinfo=UTC),)
    assert result.next_due == datetime(2027, 1, 1, 9, 15, tzinfo=UTC)


def test_dst_gap_is_skipped_and_repeated_time_runs_once():
    spring = draft(timezone="America/New_York", local_time=time(2, 30), catchup_days=2)
    assert next_slot(spring, datetime(2026, 3, 8, 5, tzinfo=UTC)) == datetime(
        2026, 3, 9, 6, 30, tzinfo=UTC
    )
    autumn = draft(timezone="America/New_York", local_time=time(1, 30), catchup_days=1)
    result = due_slots(
        autumn, datetime(2026, 11, 1, 0, tzinfo=UTC), datetime(2026, 11, 1, 8, tzinfo=UTC)
    )
    assert result.slots == (datetime(2026, 11, 1, 5, 30, tzinfo=UTC),)
    assert next_slot(autumn, result.slots[0]) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


def test_naive_clock_rejected():
    value = draft()
    with pytest.raises(DataFailure, match="invalid_input"):
        next_slot(value, datetime(2026, 10, 14))


def test_authorized_creation_uses_shared_clock_before_persistence():
    from types import SimpleNamespace

    from qtrade_data_core.schedules import ScheduleControls
    from qtrade_data_http.client import StaticScopeAuthorizer

    value = draft()
    writes = []
    store = SimpleNamespace(create=lambda definition, due: writes.append((definition, due)))
    auth = StaticScopeAuthorizer({f"{value.actor_id}:{value.scope_id}": "manage"})
    now = datetime(2026, 10, 14, 9, 15, tzinfo=UTC)
    ScheduleControls(auth, store).create(value, now)
    assert writes == [(value, datetime(2026, 10, 15, 9, 15, tzinfo=UTC))]
