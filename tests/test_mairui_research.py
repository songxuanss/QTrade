"""Recorded financial semantics through a controlled HTTP transport, not live E2E."""

import httpx
import pytest
from test_research_evidence import NOW
from test_research_interface import request

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchService
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_mairui.research import MaiRuiFinancial


def adapter(tmp_path, body):
    def reply(req):
        assert req.url.params["st"] == "20150101"
        assert req.url.params["et"] == "20151231"
        return httpx.Response(200, content=body)

    return MaiRuiFinancial(
        MaiRuiCollection(
            "fixture-only",
            cache=tmp_path,
            client=httpx.Client(transport=httpx.MockTransport(reply)),
        ),
        "income",
    )


def test_precise_values_revisions_and_unknown_units(tmp_path):
    p = adapter(
        tmp_path,
        b'[{"jzrq":"2015-06-30","plrq":"2015-08-28",'
        b'"jbmgsy":6.910000000000000001,"jlr":null,"yycb":"-"},{"jzrq":"2015-06-30",'
        b'"plrq":"2016-08-27","jbmgsy":6.28,"jlr":0}]',
    )
    req = request(source=p.source)
    auth = StaticScopeAuthorizer({f"{req.actor_id}:{req.scope_id}": "manage"})
    plan = ResearchService(auth, (p,)).preview(req)
    page = p.fetch(req, plan.partitions[0], None)
    archive = FileResearchArchive(tmp_path)
    raw = archive.capture(plan, plan.partitions[0], page, NOW)
    values = p.normalize(req, page, NOW)
    assert str(values[0].metrics[0].original_value) == "6.910000000000000001"
    assert values[0].knowledge.status == "unknown"
    assert values[0].report_period == values[1].report_period
    assert values[0].content_version != values[1].content_version
    assert values[0].metrics[1].missing_reason == "provider_null"
    assert values[1].metrics[1].original_value == 0
    assert (
        next(m for m in values[0].metrics if m.field_id == "yycb").missing_reason == "not_reported"
    )
    assert all(m.standard_value is None for v in values for m in v.metrics)
    assert archive.raw(raw) == page.rows


def test_malformed_report_preserves_raw_and_rejects_conversion(tmp_path):
    p = adapter(tmp_path, b'[{"jzrq":"broken","plrq":null,"jlr":1}]')
    req = request(source=p.source)
    part = next(p.partitions(req))
    page = p.fetch(req, part, None)
    with pytest.raises(DataFailure, match="unverified"):
        p.normalize(req, page, NOW)


def test_permission_error_does_not_become_empty_financial_success(tmp_path):
    p = adapter(tmp_path, b'{"code":403,"message":"denied"}')
    req = request(source=p.source)
    with pytest.raises(DataFailure, match="unauthorized"):
        p.fetch(req, next(p.partitions(req)), None)
