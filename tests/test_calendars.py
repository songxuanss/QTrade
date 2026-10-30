from dataclasses import replace
from datetime import UTC, date, datetime, time

import pytest

from qtrade_domain.calendars import DayOverride, TradingCalendar, TradingSession
from qtrade_domain.ports import ErrorCode, PortError


def calendar(timezone="America/New_York", opens=time(9, 30), closes=time(16)):
    # Synthetic schedule; not an authoritative exchange holiday calendar.
    return TradingCalendar(
        "test",
        1,
        timezone,
        date(2026, 1, 1),
        date(2026, 12, 31),
        frozenset(range(5)),
        (TradingSession(opens, closes),),
    )


def test_open_inclusive_close_exclusive_weekend_and_explicit_holiday():
    value = replace(calendar(), overrides=(DayOverride(date(2026, 3, 10), ()),))
    assert value.active_window(datetime(2026, 3, 9, 13, 30, tzinfo=UTC)) is not None
    assert value.active_window(datetime(2026, 3, 9, 13, 29, tzinfo=UTC)) is None
    assert value.active_window(datetime(2026, 3, 9, 20, tzinfo=UTC)) is None
    assert value.active_window(datetime(2026, 3, 7, 15, tzinfo=UTC)) is None
    assert value.active_window(datetime(2026, 3, 10, 15, tzinfo=UTC)) is None


def test_split_sessions_and_early_close_override():
    value = replace(
        calendar("Asia/Shanghai", time(9, 30), time(11, 30)),
        sessions=(
            TradingSession(time(9, 30), time(11, 30)),
            TradingSession(time(13), time(15)),
        ),
        overrides=(DayOverride(date(2026, 5, 8), (TradingSession(time(9, 30), time(11)),)),),
    )
    assert value.active_window(datetime(2026, 5, 7, 4, tzinfo=UTC)) is None
    assert value.active_window(datetime(2026, 5, 7, 5, tzinfo=UTC)) is not None
    assert value.active_window(datetime(2026, 5, 8, 3, tzinfo=UTC)) is None


def test_cross_market_dst_offsets_change_on_different_dates():
    us = calendar()
    uk = calendar("Europe/London", time(8), time(16, 30))
    jp = calendar("Asia/Tokyo", time(9), time(15))
    before, between, after = date(2026, 3, 6), date(2026, 3, 9), date(2026, 3, 30)
    assert [us.windows(day)[0].opens_at.hour for day in (before, between, after)] == [14, 13, 13]
    assert [uk.windows(day)[0].opens_at.hour for day in (before, between, after)] == [8, 8, 7]
    assert [jp.windows(day)[0].opens_at.hour for day in (before, between, after)] == [0, 0, 0]


def test_overnight_session_retains_prior_trading_date_across_local_midnight():
    value = replace(calendar("Asia/Tokyo"), sessions=(TradingSession(time(22), time(2), 1),))
    window = value.active_window(datetime(2026, 5, 8, 16, tzinfo=UTC))
    assert window is not None and window.trading_date == date(2026, 5, 8)
    assert value.active_window(datetime(2026, 5, 8, 17, tzinfo=UTC)) is None


def test_nonexistent_and_ambiguous_local_boundaries_fail_closed():
    value = replace(calendar(opens=time(2, 30), closes=time(4)), weekdays=frozenset(range(7)))
    with pytest.raises(PortError) as gap:
        value.windows(date(2026, 3, 8))
    assert gap.value.code == ErrorCode.INVALID_INPUT
    value = replace(value, sessions=(TradingSession(time(1, 30), time(3)),))
    with pytest.raises(PortError):
        value.windows(date(2026, 11, 1))
    first = replace(value, sessions=(TradingSession(time(1, 30), time(3), open_fold=0),))
    second = replace(value, sessions=(TradingSession(time(1, 30), time(3), open_fold=1),))
    assert first.windows(date(2026, 11, 1))[0].opens_at.hour == 5
    assert second.windows(date(2026, 11, 1))[0].opens_at.hour == 6


def test_invalid_overlap_naive_time_and_unknown_coverage_are_rejected():
    value = replace(
        calendar(), sessions=(TradingSession(time(9), time(12)), TradingSession(time(11), time(16)))
    )
    with pytest.raises(PortError):
        value.windows(date(2026, 5, 7))
    value = calendar()
    with pytest.raises(PortError):
        value.active_window(datetime(2026, 5, 7, 12))
    with pytest.raises(PortError) as coverage:
        value.active_window(datetime(2027, 1, 1, 12, tzinfo=UTC))
    assert coverage.value.code == ErrorCode.UNAVAILABLE


def test_market_state_service_keeps_versions_and_workspaces_separate():
    from uuid import uuid4

    from qtrade_application.calendars import CalendarService
    from qtrade_domain.authorization import WorkspaceAccess
    from qtrade_domain.identity import Role, Workspace
    from qtrade_mock.calendars import MockCalendarStore

    workspace = Workspace(
        uuid4(), "Calendar test", "organization", None, False, False, datetime.now(UTC)
    )
    admin = WorkspaceAccess(workspace, uuid4(), Role.ADMIN)
    viewer = replace(admin, role=Role.VIEWER)
    service = CalendarService(MockCalendarStore())
    first = calendar()
    with pytest.raises(PortError) as denied:
        service.put(viewer, first)
    assert denied.value.code == ErrorCode.REJECTED
    service.put(admin, first)
    service.put(admin, first)
    with pytest.raises(PortError) as conflict:
        service.put(admin, replace(first, sessions=(TradingSession(time(10), time(16)),)))
    assert conflict.value.code == ErrorCode.CONFLICT
    second = replace(first, version=2, sessions=(TradingSession(time(10), time(16)),))
    service.put(admin, second)
    at = datetime(2026, 3, 9, 13, 45, tzinfo=UTC)
    assert service.state(viewer, "test", at, version=1).is_open
    assert not service.state(viewer, "test", at).is_open
    assert service.state(viewer, "test", at).calendar_version == 2
    assert len(service.sessions(viewer, "test", date(2026, 3, 6), date(2026, 3, 10))) == 2
    outsider = replace(viewer, workspace=replace(workspace, workspace_id=uuid4()))
    with pytest.raises(PortError) as missing:
        service.state(outsider, "test", at)
    assert missing.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(PortError):
        service.sessions(viewer, "test", date(2026, 3, 10), date(2026, 3, 6))


def test_calendar_contract_roundtrip_preserves_overrides_and_explicit_dst_fold():
    from uuid import uuid4

    from qtrade_contracts import CalendarSchedule
    from qtrade_domain.calendar_contracts import from_contract, to_contract
    from qtrade_domain.calendars import CalendarSnapshot

    value = replace(
        calendar(),
        weekdays=frozenset(range(7)),
        overrides=(
            DayOverride(date(2026, 11, 1), (TradingSession(time(1, 30), time(3), open_fold=1),)),
            DayOverride(date(2026, 5, 8), ()),
        ),
    )
    snapshot = CalendarSnapshot(uuid4(), value)
    document = to_contract(snapshot)
    restored = from_contract(CalendarSchedule.model_validate_json(document.model_dump_json()))
    assert restored == snapshot
    assert restored.calendar.windows(date(2026, 11, 1))[0].opens_at.hour == 6
    assert restored.calendar.windows(date(2026, 5, 8)) == ()


@pytest.mark.parametrize(
    "change",
    [
        {"timezone": "Unknown/Zone"},
        {"weekdays": [0, 0]},
        {"weekdays": [7]},
        {"sessions": [{"opens": "09:30:00+01:00", "closes": "16:00:00"}]},
        {"effective_to": "2025-01-01"},
        {"overrides": [{"trading_date": "2027-01-01", "sessions": []}]},
    ],
)
def test_calendar_wire_contract_rejects_invalid_schedule(change):
    import json
    from pathlib import Path

    from pydantic import ValidationError

    from qtrade_contracts import CalendarSchedule

    payload = json.loads(
        (Path(__file__).parent / "fixtures/contracts/CalendarSchedule.json").read_text()
    )
    payload.update(change)
    with pytest.raises(ValidationError):
        CalendarSchedule.model_validate_json(json.dumps(payload))
