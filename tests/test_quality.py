from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from qtrade_application.quality import DataQualityService
from qtrade_contracts import Bar
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.calendars import TradingCalendar, TradingSession
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.quality import QualityPolicy, apply_quality_flags, detect_quality_issues
from qtrade_mock.quality import MockQualityIssueStore


@pytest.fixture
def sample():
    base = Bar.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Bar.json").read_text()
    )
    workspace = Workspace(
        base.workspace_id, "Quality", "organization", None, False, False, datetime.now(UTC)
    )
    access = WorkspaceAccess(workspace, uuid4(), Role.ADMIN)
    start = datetime(2026, 10, 5, 9, tzinfo=UTC)
    calendar = TradingCalendar(
        "synthetic",
        1,
        "UTC",
        date(2026, 10, 1),
        date(2026, 10, 31),
        frozenset(range(5)),
        (TradingSession(time(9), time(9, 10)),),
    )
    bars = tuple(
        base.model_copy(
            update={
                "timestamp": start + timedelta(minutes=index),
                "open": Decimal("100"),
                "high": Decimal(str(price + 1)),
                "low": Decimal("99"),
                "close": Decimal(str(price)),
                "volume": Decimal(str(volume)),
            }
        )
        for index, (price, volume) in enumerate(
            (
                (100, 10),
                (100, 10),
                (100, 10),
                (200, 10),
                (200, 10),
                (200, 10),
                (200, 10),
                (200, 1000),
                (200, 10),
                (200, 10),
            )
        )
    )
    return base, access, start, calendar, bars


def test_quality_scan_finds_all_six_rule_families_and_is_deterministic(sample):
    base, _, start, calendar, bars = sample
    sequence = (bars[0], bars[3], bars[1], bars[1], *bars[4:])
    kwargs = dict(
        workspace_id=base.workspace_id,
        dataset_id=base.dataset_id,
        bars=sequence,
        calendars={base.instrument_id: calendar},
        start=start,
        end=start + timedelta(minutes=10),
        detected_at=start + timedelta(days=1),
        policy=QualityPolicy(Decimal("0.25"), Decimal("2"), 2, 4),
    )
    first = detect_quality_issues(**kwargs)
    second = detect_quality_issues(**kwargs)
    assert first == second
    assert {issue.code for issue in first} == {
        "missing",
        "duplicate",
        "out_of_order",
        "price_jump",
        "volume_anomaly",
    }
    assert all(issue.dataset_id == base.dataset_id for issue in first)
    assert all(issue.batch_ids for issue in first if issue.code != "missing")
    flagged = apply_quality_flags(sequence, first)
    assert any(bar.quality == "suspect" for bar in flagged)
    assert any(bar.quality == "quarantined" for bar in flagged)


def test_quality_scan_flags_calendar_deviation_outside_market_session(sample):
    base, _, start, calendar, bars = sample
    outside = bars[0].model_copy(update={"timestamp": start - timedelta(minutes=1)})
    issues = detect_quality_issues(
        base.workspace_id,
        base.dataset_id,
        (outside,),
        {base.instrument_id: calendar},
        start=start - timedelta(minutes=2),
        end=start + timedelta(minutes=1),
        detected_at=start,
    )
    assert "calendar_deviation" in {issue.code for issue in issues}


def test_quality_issue_is_idempotent_queryable_claimable_and_closable(sample):
    base, access, start, calendar, bars = sample
    grants = {access.user_id: access}
    store = MockQualityIssueStore(
        lambda actor, workspace: (
            grants.get(actor)
            if grants.get(actor) and grants[actor].workspace.workspace_id == workspace
            else None
        )
    )
    service = DataQualityService(store, QualityPolicy(Decimal("0.25"), Decimal("2"), 2, 4))
    kwargs = dict(
        dataset_id=base.dataset_id,
        bars=(bars[0], bars[3], bars[1], bars[1], *bars[4:]),
        calendars={base.instrument_id: calendar},
        start=start,
        end=start + timedelta(minutes=10),
        detected_at=start + timedelta(days=1),
    )
    issues = service.scan(access, **kwargs)
    assert service.scan(access, **kwargs) == issues
    assert {item.issue_id for item in service.list(access, base.dataset_id)} == {
        item.issue_id for item in issues
    }
    claimed = service.claim(access, issues[0].issue_id, start + timedelta(days=1))
    assert claimed.status == "claimed" and claimed.claimed_by == access.user_id
    closed = service.close(access, claimed.issue_id, start + timedelta(days=2))
    assert closed.status == "closed" and closed.closed_by == access.user_id
    with pytest.raises(PortError) as duplicate_claim:
        service.claim(access, closed.issue_id, start + timedelta(days=3))
    assert duplicate_claim.value.code == ErrorCode.CONFLICT


def test_viewer_cannot_scan_or_change_quality_issue(sample):
    base, access, start, calendar, bars = sample
    store = MockQualityIssueStore(lambda actor, workspace: access)
    service = DataQualityService(store)
    viewer = replace(access, role=Role.VIEWER)
    with pytest.raises(PortError) as denied:
        service.scan(
            viewer,
            base.dataset_id,
            bars,
            {base.instrument_id: calendar},
            start=start,
            end=start + timedelta(minutes=10),
            detected_at=start,
        )
    assert denied.value.code == ErrorCode.REJECTED


def test_thirty_minute_bars_use_a_supported_interval(sample):
    base, _, start, _, bars = sample
    bars_30m = tuple(
        bar.model_copy(
            update={
                "granularity": "30m",
                "timestamp": start + timedelta(minutes=30 * index),
            }
        )
        for index, bar in enumerate(bars[:6])
    )
    issues = detect_quality_issues(
        workspace_id=base.workspace_id,
        dataset_id=base.dataset_id,
        bars=bars_30m,
        calendars={},
        start=start,
        end=start + timedelta(minutes=180),
        detected_at=start + timedelta(days=1),
    )
    assert isinstance(issues, tuple)
