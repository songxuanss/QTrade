"""Forward adjustment factors must be derived from real supplier price rows."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from qtrade_data_contracts.research_transport import ResearchPlanRequest
from qtrade_data_core.ports import DataFailure
from qtrade_data_mairui.research import MaiRuiAdjustmentFactors


class Transport:
    licence = "test-licence"

    def __init__(self, adjusted, unadjusted):
        self.adjusted = adjusted
        self.unadjusted = unadjusted
        self.paths = []

    def _symbol(self, identifier):
        return "600519.SH", False

    @staticmethod
    def _rows(data):
        return data

    def _get(self, path, params=None, **_kwargs):
        self.paths.append((path, params))
        return self.adjusted if path.endswith("/f") else self.unadjusted


def rows(prices):
    return [{"d": day.isoformat(), "c": format(price, "f")} for day, price in prices.items()]


def request(start=date(2025, 1, 1), end=date(2025, 3, 1)):
    return ResearchPlanRequest(
        actor_id=uuid4(),
        scope_id=uuid4(),
        request_id=uuid4(),
        correlation_id=uuid4(),
        source="mairui.adjustment.factors",
        kind="adjustment_factor",
        instrument_ids=("600519.SH",),
        date_axis="observation_date",
        start=start,
        end=end,
    )


def test_factor_series_changes_only_on_ratio_change_days():
    adjusted = {
        date(2025, 1, 2): Decimal("10.00"),
        date(2025, 1, 3): Decimal("10.00"),
        date(2025, 1, 6): Decimal("5.00"),
        date(2025, 1, 7): Decimal("5.05"),
    }
    unadjusted = {
        date(2025, 1, 2): Decimal("10.00"),
        date(2025, 1, 3): Decimal("10.00"),
        date(2025, 1, 6): Decimal("10.00"),
        date(2025, 1, 7): Decimal("10.10"),
    }
    provider = MaiRuiAdjustmentFactors(Transport(rows(adjusted), rows(unadjusted)))
    plan = request()
    partition = next(provider.partitions(plan))
    page = provider.fetch(plan, partition, None)
    records = provider.normalize(plan, page, datetime.now(UTC))

    assert [item.observation_date for item in records] == [
        date(2025, 1, 2),
        date(2025, 1, 6),
    ]
    assert all(item.adjustment == "forward" for item in records)
    assert records[0].factor == Decimal("1.00000000")
    assert records[1].factor == Decimal("0.50000000")
    assert records[0].base_date == date(2025, 1, 2)
    assert records[0].base_value == Decimal("1.00000000")
    assert records[0].algorithm_version == "mairui.forward-ratio.v1"
    assert records[1].content_version != records[0].content_version


def test_cent_level_ratio_noise_does_not_create_factor_changes():
    adjusted = {
        date(2025, 1, 2): Decimal("10.00"),
        date(2025, 1, 3): Decimal("10.01"),
        date(2025, 1, 6): Decimal("10.02"),
    }
    unadjusted = {
        date(2025, 1, 2): Decimal("10.50"),
        date(2025, 1, 3): Decimal("10.50"),
        date(2025, 1, 6): Decimal("10.50"),
    }
    provider = MaiRuiAdjustmentFactors(Transport(rows(adjusted), rows(unadjusted)))
    plan = request()
    page = provider.fetch(plan, next(provider.partitions(plan)), None)
    records = provider.normalize(plan, page, datetime.now(UTC))

    assert [item.observation_date for item in records] == [date(2025, 1, 2)]


def test_fetch_requests_both_adjusted_and_unadjusted_daily_close():
    provider = MaiRuiAdjustmentFactors(
        Transport(
            rows({date(2025, 1, 2): Decimal("9.5")}),
            rows({date(2025, 1, 2): Decimal("10")}),
        )
    )
    plan = request()
    provider.fetch(plan, next(provider.partitions(plan)), None)
    paths = [path for path, _ in provider.transport.paths]
    assert paths == ["hsstock/history/600519/d/f", "hsstock/history/600519/d/n"]


def test_missing_or_empty_supplier_rows_fail_closed():
    provider = MaiRuiAdjustmentFactors(Transport([], []))
    plan = request()
    with pytest.raises(DataFailure):
        provider.fetch(plan, next(provider.partitions(plan)), None)


def test_partitions_reject_non_stock_identifiers():
    provider = MaiRuiAdjustmentFactors(Transport([], []))

    class IndexTransport(Transport):
        def _symbol(self, identifier):
            return "000300.SH", True

    provider = MaiRuiAdjustmentFactors(IndexTransport([], []))
    with pytest.raises(DataFailure):
        list(provider.partitions(request()))


def test_adjusted_price_requests_are_research_evidence_not_bar_collection():
    from qtrade_data_mairui.collection import request_classification

    assert request_classification("hsstock/history/600519/d/f") == ("research.data", 1)
    assert request_classification("hsstock/history/600519.SH/d/br") == ("research.data", 1)
    assert request_classification("hsstock/history/600519/d/n") == ("bars.history", 1)
