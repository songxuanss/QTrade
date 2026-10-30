from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from qtrade_domain.ingestion import CollectionRequest
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mairui import MaiRuiAShareTickProvider


def request(
    instrument="600519.SH",
    *,
    start="2026-08-14T01:00:00+00:00",
    end="2026-08-14T03:00:00+00:00",
    page_size=2,
):
    return CollectionRequest(
        uuid4(),
        uuid4(),
        MaiRuiAShareTickProvider.source,
        "mairui-default",
        instrument,
        "tick",
        datetime.fromisoformat(start),
        datetime.fromisoformat(end),
        datetime.now(UTC),
        "raw-tick.v1",
        page_size,
    )


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class FakeClient:
    def __init__(self, data):
        self.data = data
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return FakeResponse(self.data)


def test_fetch_archives_filtered_raw_ticks_and_resumes_pages():
    client = FakeClient(
        [
            {"d": "2026-08-14", "t": "09:00:00", "p": 100.2, "v": 10, "ts": "1"},
            {"d": "2026-08-14", "t": "09:30:00", "p": 100.3, "v": 20, "ts": "2"},
            {"d": "2026-08-14", "t": "10:00:00", "p": 100.4, "v": 30, "ts": "3"},
        ]
    )
    provider = MaiRuiAShareTickProvider("test-license", client=client)  # type: ignore[arg-type]
    query = request()

    first = provider.fetch(query, None)
    second = provider.fetch(query, first.next_cursor)

    assert first.next_cursor == "0:2"
    assert second.next_cursor is None
    assert [record.sequence for record in first.records] == [0, 1]
    assert b'"ts":"3"' in second.records[0].payload
    assert "600519" in client.urls[0]


def test_rejects_hong_kong_symbols_and_ticks_before_archive():
    provider = MaiRuiAShareTickProvider("test-license", client=FakeClient([]))  # type: ignore[arg-type]
    with pytest.raises(PortError) as unsupported:
        provider.fetch(request("00700.HK"), None)
    assert unsupported.value.code == ErrorCode.UNSUPPORTED

    old = request(start="2026-08-12T01:00:00+00:00", end="2026-08-12T02:00:00+00:00")
    with pytest.raises(PortError) as unavailable:
        provider.fetch(old, None)
    assert unavailable.value.code == ErrorCode.UNSUPPORTED


def test_routes_beijing_listed_symbols_to_their_market_endpoint():
    client = FakeClient([{"d": "2026-08-14", "t": "09:30:00", "p": 10.2, "v": 100, "ts": "1"}])
    provider = MaiRuiAShareTickProvider("test-license", client=client)  # type: ignore[arg-type]
    page = provider.fetch(request("920000.BJ", page_size=10), None)
    assert len(page.records) == 1
    assert "/bj/stock/real/zbjy/20260814/920000/" in client.urls[0]


def test_maps_provider_rate_limit_without_leaking_response():
    class LimitedClient:
        def get(self, url):
            return httpx.Response(429, request=httpx.Request("GET", url))

    provider = MaiRuiAShareTickProvider("secret-license", client=LimitedClient())  # type: ignore[arg-type]
    with pytest.raises(PortError) as limited:
        provider.fetch(request(), None)
    assert limited.value.code == ErrorCode.RATE_LIMITED
    assert "secret-license" not in str(limited.value)


def test_provider_does_not_impose_seven_day_range_limit():
    client = FakeClient([])
    provider = MaiRuiAShareTickProvider("test-license", client=client)  # type: ignore[arg-type]
    query = request(start="2026-08-14T01:00:00+00:00", end="2026-08-24T03:00:00+00:00")
    cursor = None
    while True:
        page = provider.fetch(query, cursor)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(client.urls) >= 10


@pytest.mark.parametrize("day", ["2026-09-19", "2026-09-20"])
def test_weekend_missing_archive_is_an_empty_day(day):
    client = FakeClient({"error": "404:资源不存在，请检查日期与股票代码是否正确"})
    provider = MaiRuiAShareTickProvider("test-license", client=client)  # type: ignore[arg-type]
    page = provider.fetch(request(start=f"{day}T01:00:00+00:00", end=f"{day}T03:00:00+00:00"), None)
    assert page.records == ()
    assert page.next_cursor is None


def test_weekday_missing_archive_remains_a_failure():
    client = FakeClient({"error": "404:资源不存在，请检查日期与股票代码是否正确"})
    provider = MaiRuiAShareTickProvider("test-license", client=client)  # type: ignore[arg-type]
    with pytest.raises(PortError) as error:
        provider.fetch(request(), None)
    assert error.value.code == ErrorCode.INVALID_INPUT
