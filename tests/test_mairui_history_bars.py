import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from qtrade_domain.batches import ArchivedBatch
from qtrade_domain.datasets import ReplayRequest
from qtrade_domain.history_sources import HistoricalSource
from qtrade_domain.ingestion import CollectionRequest
from qtrade_domain.ports import PortError
from qtrade_mairui.bar_quality import MaiRuiBarQuality
from qtrade_mairui.history_bars import MaiRuiBarTransformation, MaiRuiHistoricalBars


def query(granularity="5m"):
    return CollectionRequest(
        uuid4(),
        uuid4(),
        MaiRuiHistoricalBars.source,
        "mairui-default",
        "600519.SH",
        granularity,
        datetime(2025, 8, 31, 16, tzinfo=UTC),
        datetime(2025, 9, 2, 16, tzinfo=UTC),
        datetime.now(UTC),
        MaiRuiBarTransformation.version,
        2,
    )


def provider(rows):
    def handle(request):
        if "tcalendar" in request.url.path:
            return httpx.Response(200, json=["20250901", "20250902"])
        return httpx.Response(200, json=rows)

    return MaiRuiHistoricalBars(
        "private-test-license", client=httpx.Client(transport=httpx.MockTransport(handle))
    )


def archive(request, page, index=0):
    draft = request.batch(index, request.created_at, page.records)
    metadata = draft.metadata(object_key="batches/test.parquet", content_sha256="a" * 64)
    return ArchivedBatch(metadata, page.records)


def row(day="2025-09-01", clock="09:35:00"):
    return {"t": f"{day} {clock}", "o": 10, "h": 11, "l": 9, "c": 10, "v": 2, "a": 2000, "sf": 0}


def test_paging_retains_calendar_raw_rows_and_normalizes_close_time_and_lots():
    request = query()
    adapter = provider([row(), row("2025-09-02")])
    first = adapter.fetch(request, None)
    assert first.next_cursor == "2025-09-01:1"
    second = adapter.fetch(request, first.next_cursor)
    assert second.next_cursor is None
    batches = (archive(request, first), archive(request, second, 1))
    bars = MaiRuiBarTransformation().transform(request.dataset_id, batches)
    assert len(bars) == 2
    assert bars[0].timestamp == datetime(2025, 9, 1, 1, 30, tzinfo=UTC)
    assert bars[0].volume == 200
    assert json.loads(first.records[1].payload)["row"] == row()
    report = MaiRuiBarQuality().assess(
        ReplayRequest(
            request.workspace_id,
            request.dataset_id,
            tuple(b.metadata.batch_id for b in batches),
            request.transformation_version,
            request.start,
            request.end,
            request.created_at,
        ),
        batches,
        bars,
    )
    assert report["expected_trading_days"] == 2
    assert report["observed_trading_days"] == 2
    assert report["research_readiness"] == "limited"


def test_missing_day_is_reported_not_classified_as_suspension():
    request = query()
    page = provider([row()]).fetch(request, None)
    batches = (archive(request, page),)
    bars = MaiRuiBarTransformation().transform(request.dataset_id, batches)
    report = MaiRuiBarQuality().assess(
        ReplayRequest(
            request.workspace_id,
            request.dataset_id,
            (batches[0].metadata.batch_id,),
            request.transformation_version,
            request.start,
            request.end,
            request.created_at,
        ),
        batches,
        bars,
    )
    assert report["missing_trading_days"] == ["2025-09-02"]
    assert report["research_readiness"] == "incomplete"


def test_calendar_disagreement_and_volume_semantics_reject_publication():
    request = query()
    page = provider([row("2025-09-03")]).fetch(
        replace(request, end=datetime(2025, 9, 3, 16, tzinfo=UTC)), None
    )
    batches = (archive(request, page),)
    bars = MaiRuiBarTransformation().transform(request.dataset_id, batches)
    with pytest.raises(PortError):
        MaiRuiBarQuality().assess(
            ReplayRequest(
                request.workspace_id,
                request.dataset_id,
                (batches[0].metadata.batch_id,),
                request.transformation_version,
                request.start,
                datetime(2025, 9, 3, 16, tzinfo=UTC),
                request.created_at,
            ),
            batches,
            bars,
        )
    bad = provider([{**row(), "v": 200}]).fetch(request, None)
    with pytest.raises(PortError, match="volume unit"):
        MaiRuiBarTransformation().transform(request.dataset_id, (archive(request, bad),))


def test_unentitled_one_minute_is_rejected_before_source_call():
    request = query("1m")
    with pytest.raises(PortError):
        provider([]).fetch(request, None)
    with pytest.raises(PortError):
        HistoricalSource(MaiRuiHistoricalBars.source, "mairui-default", ("5m", "1d")).validate(
            request
        )


def test_entitlement_failure_never_includes_secret_in_error():
    def handle(request):
        return httpx.Response(403, text="source failure")

    adapter = MaiRuiHistoricalBars(
        "private-test-license", client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    with pytest.raises(PortError) as error:
        adapter.fetch(query(), None)
    assert "private-test-license" not in str(error.value)


def test_daily_bars_are_midnight_beijing_and_not_shifted_by_minutes():
    request = query("1d")
    page = provider([{**row(), "t": "2025-09-01"}]).fetch(request, None)
    bars = MaiRuiBarTransformation().transform(request.dataset_id, (archive(request, page),))
    assert bars[0].timestamp == datetime(2025, 8, 31, 16, tzinfo=UTC)


def test_validated_calendar_is_reused_but_history_is_fetched_for_each_request():
    calls = []

    def handle(request):
        calls.append(request.url.path.split("/")[1])
        return httpx.Response(200, json=["20250901"] if "tcalendar" in request.url.path else [])

    adapter = MaiRuiHistoricalBars(
        "private-test-license", client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    adapter.fetch(query(), None)
    adapter.fetch(query(), None)
    assert calls.count("tcalendar") == 1
    assert calls.count("hsstock") == 2


def test_provider_rate_limit_retains_safe_retry_deadline():
    adapter = MaiRuiHistoricalBars(
        "private-test-license",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(429, headers={"Retry-After": "90"})
            )
        ),
    )
    with pytest.raises(PortError) as error:
        adapter.fetch(query(), None)
    assert error.value.retry_after == 90
