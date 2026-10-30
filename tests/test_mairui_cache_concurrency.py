"""Controlled provider delays verify isolation, not live supplier availability."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import httpx

from qtrade_data_mairui.collection import MaiRuiCollection


def test_slow_factor_catalog_does_not_block_names_calendar_or_annual_history(tmp_path):
    entered, release = Event(), Event()

    def respond(req):
        if "/factor/list/" in req.url.path:
            entered.set()
            assert release.wait(5), "test did not release provider call"
            return httpx.Response(
                200,
                json={"data": [{"factor_id": "pe_ttm", "name": "PE", "category": "valuation"}]},
            )
        if "/hslt/list/" in req.url.path:
            return httpx.Response(200, json=[{"dm": "601288", "jys": "SH", "mc": "农业银行"}])
        if "/tcalendar/" in req.url.path:
            return httpx.Response(200, json=["20250901"])
        return httpx.Response(200, json=[{"t": "2025-09-01 09:35:00", "c": 6}])

    provider = MaiRuiCollection(
        "fixture", cache=tmp_path, client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        factors = pool.submit(provider.factors)
        try:
            assert entered.wait(2)
            names = pool.submit(provider.names, ("601288.SH",))
            calendar = pool.submit(provider._calendar, 2025)
            annual = pool.submit(provider._full_year, "hsstock", "601288.SH", "5", 2025, False)
            # These must complete while the factor HTTP request remains suspended.
            assert names.result(timeout=2) == {"601288.SH": "农业银行"}
            assert calendar.result(timeout=2) == ["2025-09-01"]
            assert annual.result(timeout=2)[0]["c"] == 6
            assert not factors.done()
        finally:
            release.set()
        assert factors.result(timeout=2)[0].factor_id == "pe_ttm"


def test_same_annual_resource_fetches_once_and_captures_for_both_callers(tmp_path):
    entered, release = Event(), Event()
    guard = Lock()
    calls = []
    captures = [[], []]

    def respond(req):
        with guard:
            calls.append("history")
        entered.set()
        assert release.wait(5)
        return httpx.Response(200, json=[{"t": "2025-09-01 09:35:00", "c": 6}])

    provider = MaiRuiCollection(
        "fixture", cache=tmp_path, client=httpx.Client(transport=httpx.MockTransport(respond))
    )

    def fetch(index):
        return provider._full_year(
            "hsstock",
            "601288.SH",
            "5",
            2025,
            False,
            lambda kind, payload: captures[index].append((kind, payload)),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(fetch, 0)
        try:
            assert entered.wait(2)
            second = pool.submit(fetch, 1)
        finally:
            release.set()
        assert first.result(timeout=2) == second.result(timeout=2)
    assert calls == ["history"]
    assert [[kind for kind, _ in items] for items in captures] == [
        ["http_response", "history"],
        ["cached_history"],
    ]
    assert len(list(tmp_path.glob("year-*.json"))) == 1
