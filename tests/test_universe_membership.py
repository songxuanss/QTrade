from datetime import UTC, datetime, timedelta

import pytest

from qtrade_domain.ports import PortError
from qtrade_domain.universe import (
    MembershipWindow,
    coverage_for,
    disclosure,
    membership_allows,
)

START = datetime(2025, 1, 1, tzinfo=UTC)


def window(instrument: str, start: datetime, end: datetime) -> MembershipWindow:
    return MembershipWindow(instrument, start, end)


def test_membership_interval_is_half_open_on_the_exit_date():
    intervals = (window("600519.SH", START, START + timedelta(days=30)),)

    assert membership_allows(intervals, START)
    assert membership_allows(intervals, START + timedelta(days=29, hours=23))
    assert not membership_allows(intervals, START + timedelta(days=30))


def test_coverage_separates_full_partial_and_unknown_instruments():
    coverage = coverage_for(
        ("600519.SH", "600036.SH", "600000.SH"),
        (
            window("600519.SH", START, START + timedelta(days=365)),
            window("600036.SH", START, START + timedelta(days=100)),
        ),
        START,
        START + timedelta(days=365),
    )

    assert (coverage.instruments, coverage.full_coverage) == (3, 1)
    assert coverage.partial_coverage == 1
    assert coverage.unknown == 1


def test_membership_windows_must_be_ordered_and_tolerant_of_exact_boundaries():
    with pytest.raises(PortError):
        window("600519.SH", START, START)
    with pytest.raises(PortError):
        window("", START, START + timedelta(days=1))
    with pytest.raises(PortError):
        coverage_for(("600519.SH",), (), START + timedelta(days=1), START)


def test_disclosure_names_survivorship_risk_in_both_modes():
    coverage = coverage_for(("600519.SH",), (), START, START + timedelta(days=30))
    dataset_mode = disclosure("dataset", coverage)
    membership_mode = disclosure("membership", coverage)

    assert "幸存者偏差" in dataset_mode
    assert "未启用成员区间校验" in dataset_mode
    assert "强制平仓" in membership_mode
    assert "1 个未知" in membership_mode
    with pytest.raises(PortError):
        disclosure("guess", coverage)
