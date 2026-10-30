"""Controlled HTTP boundary checks; these do not establish live supplier readiness."""

from base64 import b64decode
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from qtrade_data_contracts.collection import CollectionJobRequest, FactorDefinition
from qtrade_data_contracts.research_transport import ResearchPlanRequest
from qtrade_data_core.collection import CollectionPart
from qtrade_data_core.ports import DataFailure
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_mairui.research import MaiRuiDistributions, MaiRuiFinancial


def provider(tmp_path, body):
    return MaiRuiCollection(
        "fixture-only",
        cache=tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        ),
    )


@pytest.mark.parametrize(
    "body",
    [
        b'{"code":403,"code":200,"data":[]}',
        b'{"data":[{"value":1,"value":2}]}',
        b'{"data":[{"value":1,"\\u0076alue":2}]}',
        b'{"data":[{"metadata":{"unit":"CNY","unit":"USD"}}]}',
        b'{"data":[{"value":NaN}]}',
        b'{"data":[{"value":Infinity}]}',
        b'{"data":[{"value":-Infinity}]}',
        b'{"code":false,"data":[]}',
        b'{"code":0.0,"data":[]}',
        b'{"code":null,"data":[]}',
        b'{"code":"200","data":[]}',
        b'{"code":[],"data":[]}',
        b'{"code":{},"data":[]}',
        b'{"data":[{"value":1e999999999999999999999}]}',
    ],
)
def test_ambiguous_response_is_captured_exactly_then_rejected(tmp_path, body, caplog):
    caplog.set_level("INFO", logger="qtrade_data_mairui.collection")
    captures = []
    with pytest.raises(DataFailure, match="^unavailable$") as error:
        provider(tmp_path, body)._get(
            "factor/history/pe_ttm/601288", capture=lambda *v: captures.append(v)
        )
    assert str(error.value) == "unavailable"
    assert len(captures) == 1
    kind, envelope = captures[0]
    assert kind == "http_response"
    assert envelope["http_status"] == 200 and envelope["complete"] is True
    assert b64decode(envelope["body_base64"]) == body
    assert set(envelope) == {"http_status", "operation", "body_base64", "complete"}
    assert "fixture-only" not in caplog.text and body.decode() not in caplog.text
    assert caplog.records[-1].error_code == "unavailable"


@pytest.mark.parametrize(
    "code,expected",
    [(401, "unauthorized"), (403, "unauthorized"), (108, "unauthorized"), (429, "rate_limited")],
)
def test_integer_business_errors_keep_classification_after_capture(tmp_path, code, expected):
    captures = []
    body = f'{{"code":{code},"data":[]}}'.encode()
    with pytest.raises(DataFailure, match=f"^{expected}$"):
        provider(tmp_path, body)._get(
            "hsstock/history/601288.SH/d/n", capture=lambda *v: captures.append(v)
        )
    assert len(captures) == 1 and b64decode(captures[0][1]["body_base64"]) == body


@pytest.mark.parametrize("prefix", [b'{"data":', b'{"code":0,"data":', b'{"code":200,"data":'])
def test_valid_rows_preserve_decimal_precision_and_numeric_text(tmp_path, prefix):
    body = prefix + b'[{"value":1.234567890123456789,"signal":"1.0000","null":null,"zero":0}]}'
    result = provider(tmp_path, body)._get("factor/history/pe_ttm/601288")
    assert result["data"] == [
        {"value": Decimal("1.234567890123456789"), "signal": "1.0000", "null": None, "zero": 0}
    ]


def test_archive_failure_prevents_json_interpretation(tmp_path):
    # The archive's safe failure wins over even a provider authorization error:
    # callers cannot continue using a response whose evidence was not retained.
    def failed_capture(*_):
        raise DataFailure("conflict")

    with pytest.raises(DataFailure, match="^conflict$"):
        provider(tmp_path, b'{"code":403}')._get("hscp/jnfh/601288", capture=failed_capture)


def test_rejected_json_finalizes_charged_attempt_as_failure(tmp_path):
    body = b'{"code":0,"data":[{"value":1,"value":2}]}'
    outcomes = []
    attempt = SimpleNamespace(
        lease=SimpleNamespace(request=SimpleNamespace(request_id=uuid4())),
        finish=lambda size, error: outcomes.append((size, error)),
    )
    source = provider(tmp_path, body)
    source.usage = SimpleNamespace(begin=lambda operation, credits: attempt)
    with pytest.raises(DataFailure, match="^unavailable$"):
        source._get("factor/history/pe_ttm/601288")
    assert outcomes == [(len(body), "unavailable")]


def test_excessive_nesting_is_safe_failure_with_exact_capture(tmp_path, caplog):
    # Python 3.12 also has a C recursion bound independent of the Python limit.
    # This small byte payload exceeds both without changing global limits.
    depth = 100_000
    test_ambiguous_response_is_captured_exactly_then_rejected(
        tmp_path, b"[" * depth + b"0" + b"]" * depth, caplog
    )


@pytest.mark.parametrize("kind", ["bars", "factors"])
def test_prepared_collection_rejects_ambiguous_rows_before_conversion(tmp_path, kind):
    body = b'[{"t":"2025-09-01","value":1,"value":2}]'
    source = provider(tmp_path, body)
    # Controlled cached context isolates the history response being tested.
    source._calendars[2025] = ["2025-09-01"]
    source._factor_cache = (
        monotonic() + 900,
        (
            FactorDefinition(
                factor_id="pe_ttm",
                name="PE",
                category="valuation",
                unit="unknown",
                description="",
                calc_version="unknown",
                update_frequency="unknown",
                available=True,
            ),
        ),
    )
    start = datetime(2025, 9, 1, tzinfo=UTC)
    req = CollectionJobRequest(
        schema_version=1,
        scope_id=uuid4(),
        actor_id=uuid4(),
        request_id=uuid4(),
        correlation_id=uuid4(),
        source="mairui.history.bars" if kind == "bars" else "mairui.factor.history",
        dataset_kind=kind,
        instrument_ids=("601288.SH",),
        granularity="1d",
        fields=() if kind == "bars" else ("pe_ttm",),
        start=start,
        end=start + timedelta(days=1),
    )
    task = CollectionPart(
        uuid4(),
        req.request_id,
        req,
        "601288.SH",
        None if kind == "bars" else "pe_ttm",
        req.start,
        req.end,
        "mairui.history-bars.v3" if kind == "bars" else "mairui.factor-history.v1",
    )
    captures, checkpoints, converted = [], [], []
    source.normalize = lambda *v: converted.append(v)
    with pytest.raises(DataFailure, match="^unavailable$"):
        source.fetch_prepared(
            task, capture=lambda *v: captures.append(v), prepared=checkpoints.append
        )
    assert converted == checkpoints == []
    assert len(captures) == 1 and captures[0][0] == "http_response"
    assert b64decode(captures[0][1]["body_base64"]) == body


@pytest.mark.parametrize("kind", ["financial_statement", "corporate_action"])
def test_research_fetch_keeps_response_but_produces_no_ambiguous_page(tmp_path, kind):
    body = b'[{"jzrq":"2025-06-30","amount":1,"amount":2}]'
    transport = provider(tmp_path, body)
    source = (
        MaiRuiFinancial(transport, "income")
        if kind == "financial_statement"
        else MaiRuiDistributions(transport)
    )
    req = ResearchPlanRequest(
        actor_id=uuid4(),
        scope_id=uuid4(),
        request_id=uuid4(),
        correlation_id=uuid4(),
        source=source.source,
        kind=kind,
        instrument_ids=("601288.SH",),
        date_axis="report_period" if kind == "financial_statement" else "event_date",
        start=date(2025, 1, 1),
        end=date(2026, 1, 1),
    )
    captures = []
    with pytest.raises(DataFailure, match="^unavailable$"):
        source.fetch_with_capture(req, next(source.partitions(req)), None, captures.append)
    assert len(captures) == 1
    assert captures[0].body == body and captures[0].complete is True
