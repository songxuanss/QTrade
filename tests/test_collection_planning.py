"""Temporal policy contracts: provider limits, market-year boundaries and configured caps."""

from datetime import UTC, datetime, timedelta

import pytest
from test_independent_collection import request

from qtrade_data_core.planning import TimePartition
from qtrade_data_mairui.collection import MaiRuiCollection


@pytest.mark.parametrize("year", [2024, 2025])
def test_pro_cross_year_uses_local_year_not_seven_days(tmp_path, year):
    provider = MaiRuiCollection("fixture", pro=True, cache=tmp_path)
    start = datetime(year, 12, 20, tzinfo=UTC)
    end = datetime(year + 1, 2, 15, tzinfo=UTC)
    spans = list(provider.partitions(request(), start, end))
    boundary = datetime(year, 12, 31, 16, tzinfo=UTC)
    assert spans == [(start, boundary), (boundary, end)]
    assert spans[1][1] - spans[1][0] > timedelta(days=7)


def test_factor_source_bound_remains_when_operator_limit_unlimited(tmp_path):
    provider = MaiRuiCollection("fixture", pro=True, cache=tmp_path)
    req = request(
        source="mairui.factor.history", dataset_kind="factors", fields=("pe_ttm",), granularity="1d"
    )
    start, end = datetime(2024, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
    spans = list(provider.partitions(req, start, end))
    assert spans[0][0] == start and spans[-1][1] == end
    assert all(right - left <= timedelta(days=365) for left, right in spans)
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:], strict=False))


def test_operator_cap_only_tightens_policy(tmp_path):
    provider = MaiRuiCollection("fixture", pro=True, cache=tmp_path, partition_max_days=2)
    req = request()
    spans = list(provider.partitions(req, req.start, req.end))
    assert len(spans) == 8
    assert all(right - left == timedelta(days=2) for left, right in spans)
    with pytest.raises(ValueError):
        TimePartition("Asia/Shanghai", max_days=-1)
    with pytest.raises(ValueError):
        list(TimePartition("Asia/Shanghai").spans(req.start.replace(tzinfo=None), req.end))


def test_daily_and_regular_minute_strategies_differ(tmp_path):
    provider = MaiRuiCollection("fixture", cache=tmp_path)
    minute = request(source="mairui.history.bars")
    daily = minute.model_copy(update={"granularity": "1d"})
    assert len(list(provider.partitions(minute, minute.start, minute.end))) == 3
    assert len(list(provider.partitions(daily, daily.start, daily.end))) == 1
