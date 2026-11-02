"""Opt-in public-page rights terms: two-source agreement, fail closed.

No live call is made here. Fixtures use the field shapes observed on
2026-11-01; the acceptance run is recorded in
docs/backtesting/QUANT-MODEL-READINESS.md.
"""

import json
from datetime import UTC, date, datetime

import httpx
import pytest
from test_research_interface import request

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchRawPage
from qtrade_data_http.rights import (
    MAPPING,
    SOURCE,
    PublicWebRightsTerms,
    _BoundedHttp,
    code_of,
    source_profile,
)

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)

SINA_HTML = """<html><body>
<!--分红 begin-->
<table id="sharebonus_1"><tbody><tr><td>ignored</td></tr></tbody></table>
<!--配股 begin-->
<table id="sharebonus_2">
  <thead>
    <tr><th colspan='11'>配股</td></tr>
    <tr>
      <th><strong>公告日期</strong></td><th><strong>配股方案(每10股配股股数)</strong></td>
      <th><strong>配股价格(元)</strong></td><th><strong>基准股本(股)</strong></td>
      <th><strong>除权日</strong></td><th><strong>股权登记日</strong></td>
      <th><strong>缴款起始日</strong></td><th><strong>缴款终止日</strong></td>
      <th><strong>配股上市日</strong></td><th><strong>募集资金合计(元)</strong></td>
      <th><strong>查看详细</strong></td>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>2013-08-23</td><td>1.74</td><td>9.29</td><td>17666100000</td>
      <td>2013-09-05</td><td>2013-08-27</td><td>2013-08-28 00:00:00</td>
      <td>2013-09-03</td><td>2013-09-11</td><td>&nbsp;</td>
      <td><a href="/corp/view/1">查看</a></td>
    </tr>
    <tr>
      <td>2010-03-02</td><td>1.3</td><td>8.85</td><td>15658900000</td>
      <td>2010-03-15</td><td>2010-03-04</td><td>2010-03-05 00:00:00</td>
      <td>2010-03-11</td><td>2010-03-19</td><td></td>
      <td><a href="/corp/view/2">查看</a></td>
    </tr>
  </tbody>
</table>
<!--配股 end-->
</body></html>"""

SINA_EMPTY = """<table id="sharebonus_2"><thead><tr><th>公告日期</th></tr></thead>
<tbody><tr><td colspan='11' style='text-align:center'>暂时没有数据！</td></tr></tbody></table>"""

EASTMONEY_JSON = {
    "version": "x",
    "success": True,
    "message": "ok",
    "code": 0,
    "result": {
        "pages": 1,
        "count": 2,
        "data": [
            {
                "SECURITY_CODE": "600036",
                "PLACING_RATIO": 1.74,
                "ISSUE_PRICE": 9.29,
                "EQUITY_RECORD_DATE": "2013-08-27 00:00:00",
                "EX_DIVIDEND_DATE": "2013-09-05 00:00:00",
                "LISTING_DATE": "2013-09-11 00:00:00",
                "PAY_START_DATE": "2013-08-28 00:00:00",
                "PAY_END_DATE": "2013-09-03 00:00:00",
                "FIRST_NOTICE_DATE": "2013-08-23 00:00:00",
            },
            {
                "SECURITY_CODE": "600036",
                "PLACING_RATIO": 1.3,
                "ISSUE_PRICE": 8.85,
                "EQUITY_RECORD_DATE": "2010-03-04 00:00:00",
                "EX_DIVIDEND_DATE": "2010-03-15 00:00:00",
                "LISTING_DATE": "2010-03-19 00:00:00",
                "PAY_START_DATE": "2010-03-05 00:00:00",
                "PAY_END_DATE": "2010-03-11 00:00:00",
                "FIRST_NOTICE_DATE": "2010-03-02 00:00:00",
            },
        ],
    },
}


def provider() -> PublicWebRightsTerms:
    return PublicWebRightsTerms(configured=True)


def req(**updates):
    return request(
        **(
            dict(
                source=SOURCE,
                kind="corporate_action",
                date_axis="event_date",
                instrument_ids=("600036.SH",),
                start=date(2013, 1, 1),
                end=date(2014, 1, 1),
            )
            | updates
        )
    )


def rows(
    eastmoney: bytes | None = None, sina: bytes | None = None, identifier: str = "600036.SH"
) -> ResearchRawPage:
    p = provider()
    values: list[dict[str, object]] = []
    if eastmoney is not None:
        values.extend(p._eastmoney_rows(eastmoney, identifier))
    if sina is not None:
        values.extend(p._sina_rows(sina, identifier))
    return ResearchRawPage(tuple(values), None, None)


def eastmoney(document: dict[str, object] | None = None) -> bytes:
    return json.dumps(document or EASTMONEY_JSON).encode()


def test_matching_sources_publish_rights_terms():
    records = provider().normalize(req(), rows(eastmoney(), SINA_HTML.encode("gb18030")), NOW)
    assert len(records) == 1
    record = records[0]
    assert record.action == "rights"
    assert record.instrument_id == "600036.SH"
    assert record.announcement_date == date(2013, 8, 23)
    assert record.record_date == date(2013, 8, 27)
    assert record.ex_date == date(2013, 9, 5)
    assert record.listing_date == date(2013, 9, 11)
    assert record.payment_date is None
    assert record.status == "implemented"
    assert record.basis_shares == 10
    assert format(record.rights_shares, "f") == "1.74"
    assert format(record.rights_price, "f") == "9.29"
    assert record.mapping_version == MAPPING
    assert "eastmoney" in record.terms_evidence and "sina" in record.terms_evidence
    assert record.knowledge.status == "unknown"


def test_range_filters_by_announcement_date_before_reconciling():
    wide = provider().normalize(
        req(start=date(2010, 1, 1), end=date(2014, 1, 1)),
        rows(eastmoney(), SINA_HTML.encode("gb18030")),
        NOW,
    )
    assert [r.ex_date for r in wide] == [date(2010, 3, 15), date(2013, 9, 5)]
    assert (
        provider().normalize(
            req(start=date(2020, 1, 1), end=date(2021, 1, 1)),
            rows(eastmoney(), SINA_HTML.encode("gb18030")),
            NOW,
        )
        == ()
    )


def test_no_observations_in_either_source_is_empty_not_an_error():
    empty = json.dumps({"success": False, "code": 9201, "result": None}).encode()
    assert provider().normalize(req(), rows(empty, SINA_EMPTY.encode("gb18030")), NOW) == ()


def test_one_sided_event_fails_closed():
    with pytest.raises(DataFailure, match="unverified"):
        provider().normalize(req(), rows(eastmoney(), None), NOW)
    with pytest.raises(DataFailure, match="unverified"):
        provider().normalize(req(), rows(None, SINA_HTML.encode("gb18030")), NOW)


def test_single_field_disagreement_fails_closed():
    changed = json.loads(json.dumps(EASTMONEY_JSON))
    changed["result"]["data"][0]["ISSUE_PRICE"] = 9.3
    with pytest.raises(DataFailure, match="unverified"):
        provider().normalize(req(), rows(eastmoney(changed), SINA_HTML.encode("gb18030")), NOW)


def test_placeholder_dates_are_rejected_or_unknown_never_invented():
    placeholder = json.loads(json.dumps(EASTMONEY_JSON))
    placeholder["result"]["data"][0]["FIRST_NOTICE_DATE"] = "1900-01-01 00:00:00"
    with pytest.raises(DataFailure, match="unverified"):
        provider().normalize(req(), rows(eastmoney(placeholder), SINA_HTML.encode("gb18030")), NOW)
    sina = SINA_HTML.replace("<td>2013-09-11</td>", "<td>1900-01-01</td>")
    record = provider().normalize(req(), rows(eastmoney(), sina.encode("gb18030")), NOW)[0]
    assert record.listing_date is None and record.status == "unknown"


def test_missing_source_table_and_paging_are_unverified():
    with pytest.raises(DataFailure, match="unverified"):
        provider()._sina_rows(b"<table id='sharebonus_1'></table>", "600036.SH")
    paged = json.loads(json.dumps(EASTMONEY_JSON))
    paged["result"]["pages"] = 2
    with pytest.raises(DataFailure, match="unverified"):
        provider()._eastmoney_rows(eastmoney(paged), "600036.SH")
    assert (
        provider()._eastmoney_rows(
            json.dumps({"success": False, "code": 9201, "result": None}).encode(), "600036.SH"
        )
        == []
    )


def test_duplicate_ex_date_within_one_source_is_ambiguous():
    first = EASTMONEY_JSON["result"]["data"][0]
    duplicate = {"success": True, "result": {"pages": 1, "data": [first, first]}}
    with pytest.raises(DataFailure, match="unverified"):
        provider().normalize(req(), rows(eastmoney(duplicate), SINA_HTML.encode("gb18030")), NOW)


def test_non_a_share_or_index_identifiers_are_unsupported():
    for identifier in ("600036.BJ", "index:000001.SH", "crypto:CN:OKX:BTC"):
        with pytest.raises(DataFailure, match="unsupported"):
            code_of(identifier)


def test_unconfigured_capability_needs_an_unblock_condition():
    disabled = PublicWebRightsTerms(configured=False)
    capability = disabled.capabilities()[0]
    assert capability.configured is False
    assert capability.unblock_condition
    assert capability.limitations and "内部研究" in " ".join(capability.limitations)
    with pytest.raises(DataFailure, match="unauthorized"):
        disabled.fetch(req(), next(iter(disabled.partitions(req()))), None)
    assert disabled.estimate(req(), 2).calls_max == 4


def test_source_profile_reports_every_aspect_and_the_licence_limit():
    profile = source_profile(configured=True)
    assert profile.source == SOURCE and profile.state == "configured"
    assert profile.kind == "rights_terms" and profile.mapping_version == MAPPING
    assert len(profile.facts) == 8
    licence = next(f for f in profile.facts if f.aspect == "license")
    assert "再分发" in licence.detail
    assert source_profile(configured=False).state == "unconfigured"


def test_fetch_archives_both_raw_responses_before_parsing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "datacenter-web.eastmoney.com":
            return httpx.Response(200, content=eastmoney())
        return httpx.Response(200, content=SINA_HTML.encode("gb18030"))

    captured: list[tuple[int | None, bytes]] = []
    provider_ = PublicWebRightsTerms(
        configured=True,
        http=_BoundedHttp(spacing=0, client=httpx.Client(transport=httpx.MockTransport(handler))),
    )
    request_ = req()
    partition = next(iter(provider_.partitions(request_)))
    page = provider_.fetch_with_capture(
        request_,
        partition,
        None,
        lambda response: captured.append((response.status, response.body)),
    )
    assert [status for status, _ in captured] == [200, 200]
    assert b"RPT_IPO_ALLOTMENT" in captured[0][1] or b"PLACING_RATIO" in captured[0][1]
    assert b"sharebonus_2" in captured[1][1]
    assert {row["_source"] for row in page.rows} == {"eastmoney", "sina"}
    assert len(provider_.normalize(request_, page, NOW)) == 1


def test_fetch_rejects_transport_errors_without_leaking_urls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"busy")

    provider_ = PublicWebRightsTerms(
        configured=True,
        http=_BoundedHttp(spacing=0, client=httpx.Client(transport=httpx.MockTransport(handler))),
    )
    request_ = req()
    with pytest.raises(DataFailure) as error:
        provider_.fetch(request_, next(iter(provider_.partitions(request_))), None)
    assert error.value.code == "unavailable"
    assert "http" not in str(error.value).lower()
