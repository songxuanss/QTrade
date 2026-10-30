"""Portable seam acceptance: immutable archives, precise values and vendor/error boundaries."""

import json
import logging
from base64 import b64decode
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from qtrade_collector.diagnostics import SafeJsonFormatter
from qtrade_data_contracts.collection import (
    CollectedBar,
    CollectionJobRequest,
    CollectionRecordPage,
    FactorPoint,
    digest,
    instrument_key,
)
from qtrade_data_core.collection import (
    CollectionPart,
    CollectionResult,
    CollectionService,
    subtract,
)
from qtrade_data_core.diagnostics import collection_context
from qtrade_data_core.ports import DataFailure
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_http.client import CollectorClient, StaticScopeAuthorizer
from qtrade_data_mairui.collection import MaiRuiCollection

START = datetime(2025, 9, 1, tzinfo=UTC)


def test_daily_default_uses_supplier_calendar_and_previous_year(tmp_path):
    calls = []

    def respond(req):
        calls.append(req.url.path.split("/")[-2])
        year = calls[-1]
        return httpx.Response(200, json=["20260105"] if year == "2026" else ["20251231"])

    provider = MaiRuiCollection(
        "fixture", cache=tmp_path, client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    early = provider.default_range("mairui.history.bars", "1d", datetime(2026, 1, 1, tzinfo=UTC))
    assert early.last == date(2025, 12, 31) and early.first == date(2025, 1, 1)
    assert calls == ["2026", "2025"]
    later = provider.default_range(
        "mairui.history.bars", "1d", datetime(2026, 1, 5, 9, 10, tzinfo=UTC)
    )
    assert later.last == date(2026, 1, 5) and calls == ["2026", "2025"]
    with pytest.raises(DataFailure, match="unsupported"):
        provider.default_range("mairui.factor.history", "1d", START)
    assert calls == ["2026", "2025"]


def request(**overrides):
    values = dict(
        schema_version=1,
        scope_id=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        request_id=uuid4(),
        source="mairui.pro.bars",
        dataset_kind="bars",
        instrument_ids=("601288.SH",),
        granularity="5m",
        start=START,
        end=START + timedelta(days=16),
    )
    values.update(overrides)
    return CollectionJobRequest(**values)


def part(req=None):
    req = req or request()
    return CollectionPart(
        uuid4(),
        req.request_id,
        req,
        req.instrument_ids[0],
        req.fields[0] if req.fields else None,
        req.start,
        req.end,
        "mairui.pro-bars.v1",
        "农业银行",
    )


def bar():
    return CollectedBar(
        instrument_id="601288.SH",
        timestamp=START,
        granularity="5m",
        open=Decimal("3.20"),
        high=Decimal("3.30"),
        low=Decimal("3.10"),
        close=Decimal("3.25"),
        volume=Decimal("100"),
        value=Decimal("325"),
    )


@pytest.mark.parametrize(
    "identifier,kind",
    [
        ("601288.SH", "stock"),
        ("index:000001.SH", "index"),
        ("stock:HK:XHKG:00700", "stock"),
        ("stock:US:XNAS:AAPL", "stock"),
        ("etf:CN:XSHG:510300", "etf"),
        ("future:CN:CFFEX:IF2612", "future"),
        ("future:CN:CFFEX:IF@CONT", "future"),
        ("crypto:GLOBAL:BINANCE:BTC-USDT@PERP", "crypto"),
    ],
)
def test_market_identity_extensible_but_provider_capabilities_explicit(identifier, kind, tmp_path):
    req = request(instrument_ids=(identifier,))
    assert instrument_key(identifier)[0] == kind
    assert CollectionJobRequest.model_validate_json(req.model_dump_json()) == req
    if identifier.startswith(("stock:HK", "stock:US", "etf:", "future:", "crypto:")):
        provider = MaiRuiCollection("fixture-key", pro=True, cache=tmp_path)
        service = CollectionService(None, None, None, (provider,))
        with pytest.raises(DataFailure, match="unsupported"):
            service._source(req)


def test_contract_rejects_ambiguous_identity_version_naive_range_and_bar_envelope():
    for update in (
        {"instrument_ids": ("00700",)},
        {"schema_version": 2},
        {"start": START.replace(tzinfo=None)},
        {"end": START},
    ):
        with pytest.raises(ValidationError):
            request(**update)
    with pytest.raises(ValidationError):
        CollectedBar(**(bar().model_dump() | {"high": Decimal("3.00")}))


def test_null_factor_retains_reason_and_precision():
    point = FactorPoint(
        instrument_id="601288.SH",
        factor_id="pe_ttm",
        trade_date=date(2025, 9, 1),
        value=None,
        value_kind="missing",
        missing_reason="provider_null",
        calc_version="unknown",
    )
    assert FactorPoint.model_validate_json(point.model_dump_json()).value is None
    with pytest.raises(ValidationError):
        FactorPoint(**(point.model_dump() | {"value": Decimal("0")}))
    encoded = bar().model_dump_json()
    assert '"3.25"' in encoded
    assert CollectedBar.model_validate_json(encoded).close == Decimal("3.25")


def test_archive_recovers_immutable_named_manifest_and_rejects_corruption(tmp_path):
    archive, task = FileCollectionArchive(tmp_path), part()
    result = CollectionResult(({"c": "3.25"},), (bar(),), {"volume": "shares"}, "UTC", {})
    publication = archive.publish(task, result)
    assert publication.instrument_name == "农业银行" and publication.raw_record_count == 1
    assert FileCollectionArchive(tmp_path).recover(task) == publication
    assert archive.records(publication) == (bar(),)
    assert archive.publish(task, replace(result, raw=({"c": "9"},))) == publication
    raw = (
        tmp_path
        / str(task.request.scope_id)
        / str(task.part_id)
        / f"raw-{publication.raw_sha256}.parquet"
    )
    raw.write_bytes(b"corrupt")
    with pytest.raises(DataFailure):
        archive.records(publication)
    with pytest.raises(DataFailure):
        archive.recover(task)


def test_page_checksum_no_tampering():
    data = dict(
        schema_version=1,
        scope_id=uuid4(),
        publication_id=uuid4(),
        records=(bar(),),
        offset=0,
        next_offset=None,
        page_sha256=digest([bar().model_dump(mode="json")]),
    )
    page = CollectionRecordPage(**data)
    assert CollectionRecordPage.model_validate_json(page.model_dump_json()) == page
    with pytest.raises(ValidationError):
        CollectionRecordPage(**(data | {"page_sha256": "0" * 64}))


def test_missing_intervals_union_overlaps_and_edges():
    task = part()
    occupied = (
        replace(task, start=START - timedelta(days=2), end=START + timedelta(days=3)),
        replace(task, start=START + timedelta(days=2), end=START + timedelta(days=5)),
        replace(task, start=START + timedelta(days=7), end=START + timedelta(days=20)),
    )
    assert list(subtract(START, task.end, occupied)) == [
        (START + timedelta(days=5), START + timedelta(days=7))
    ]


@pytest.mark.parametrize(
    "status,code", [(403, "unauthorized"), (429, "rate_limited"), (500, "unavailable")]
)
def test_provider_failure_no_credential_leak(tmp_path, status, code):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(status, text="private upstream text")
    )
    provider = MaiRuiCollection(
        "fixture-secret-do-not-log",
        pro=True,
        cache=tmp_path,
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(DataFailure) as caught:
        provider._get("fixture")
    assert caught.value.code == code
    assert "secret" not in str(caught.value)


def test_provider_year_cache_crops_and_reuses_precise_data(tmp_path):
    calls = []

    def response(req):
        calls.append(req.url.path)
        if "/tcalendar/" in req.url.path:
            return httpx.Response(200, json=[20250901, 20250902])
        if "/full/" in req.url.path:
            return httpx.Response(
                200,
                content=(
                    b'[{"t":"2025-09-01 09:35:00","o":3.2,"h":3.3,"l":3.1,'
                    b'"c":3.25,"v":1,"a":325},{"t":"2025-09-02 09:35:00",'
                    b'"o":3.2,"h":3.3,"l":3.1,"c":3.25,"v":1,"a":325}]'
                ),
            )
        raise AssertionError("unexpected vendor call")

    provider = MaiRuiCollection(
        "fixture-key",
        pro=True,
        cache=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(response)),
    )
    task = replace(
        part(), start=datetime(2025, 9, 1, tzinfo=UTC), end=datetime(2025, 9, 2, tzinfo=UTC)
    )
    first = provider.fetch(task)
    second = provider.fetch(task)
    assert first.records == second.records and len(first.records) == 1
    assert first.records[0].volume == Decimal("100")
    assert sum("/full/" in path for path in calls) == 1
    assert first.records[0].timestamp == datetime(2025, 9, 1, 1, 30, tzinfo=UTC)


def test_provider_factor_actual_array_shape_and_nulls(tmp_path):
    def response(req):
        if "/factor/list/" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [{"factor_id": "pe_ttm", "name": "市盈率", "category": "valuation"}],
                },
            )
        return httpx.Response(
            200, json={"code": 200, "data": [{"trade_date": "20250902", "value": None}]}
        )

    provider = MaiRuiCollection(
        "fixture-key",
        pro=True,
        cache=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(response)),
    )
    result = provider.fetch(
        part(request(dataset_kind="factors", granularity="1d", fields=("pe_ttm",)))
    )
    assert len(result.records) == 1 and result.records[0].value is None
    assert result.records[0].missing_reason == "provider_null"
    assert result.evidence["availability_evidence"] == "unknown"


def test_client_permission_boundary_and_sanitized_error():
    actor, scope = uuid4(), uuid4()
    auth = StaticScopeAuthorizer({f"{actor}:{scope}": "read"})
    auth.require(actor, scope, "read")
    for subject, action in ((actor, "manage"), (uuid4(), "read")):
        with pytest.raises(DataFailure, match="forbidden"):
            auth.require(subject, scope, action)
    for url in (
        "http://example.test",
        "https://user:pass@example.test",
        "https://example.test?key=x",
    ):
        with pytest.raises(ValueError):
            CollectorClient(url, "x" * 32)
    client = CollectorClient(
        "https://example.test",
        "x" * 32,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(403, text="secret"))
        ),
    )
    with pytest.raises(DataFailure, match="forbidden"):
        client.request("GET", "/v1/jobs", actor_id=actor, scope_id=scope)


def test_json_logs_keep_correlation_drop_exception_and_secrets():
    context = collection_context.set({"job_id": "test-job", "correlation_id": "test-correlation"})
    try:
        record = logging.LogRecord(
            "httpx", logging.ERROR, "", 1, "https://provider/private-key", (), None
        )
        record.event = "provider_request"
        record.exception = "private-key"
        value = SafeJsonFormatter().format(record)
        assert "private-key" not in value and "https://" not in value
        assert json.loads(value)["correlation_id"] == "test-correlation"
    finally:
        collection_context.reset(context)


def test_http_decoded_request_keeps_strict_json_contract_semantics():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from qtrade_api.collector import SubmitBody
    from qtrade_data_contracts.collection import CollectionWireRequest

    app = FastAPI()

    @app.post("/preview")
    def preview(body: CollectionWireRequest):
        return body.model_dump(mode="json")

    client = TestClient(app)
    value = request().model_dump(mode="json")
    response = client.post("/preview", json=value)
    assert response.status_code == 200 and response.json() == value
    assert (
        str(SubmitBody.model_validate({"request": value, "coverage_version": "x"}).request.scope_id)
        == value["scope_id"]
    )
    assert client.post("/preview", json=value | {"schema_version": 2}).status_code == 422
    assert client.post("/preview", json=value | {"extra": "x"}).status_code == 422


def test_instrument_attributes_and_derivative_product_identity_agree():
    from qtrade_data_contracts.collection import InstrumentIdentity

    identity = InstrumentIdentity(
        instrument_id="crypto:GLOBAL:BINANCE:BTC-USDT@PERP",
        asset_kind="crypto",
        market="GLOBAL",
        venue="BINANCE",
        code="BTC-USDT@PERP",
        name="BTC USDT 永续",
        currency="USDT",
        product_type="perpetual",
    )
    assert identity.currency == "USDT"
    for update in ({"asset_kind": "stock"}, {"product_type": "spot"}, {"market": "CN"}):
        with pytest.raises(ValidationError):
            InstrumentIdentity(**(identity.model_dump() | update))
    assert (
        InstrumentIdentity(
            instrument_id="future:CN:CFFEX:IF@CONT",
            asset_kind="future",
            market="CN",
            venue="CFFEX",
            code="IF@CONT",
            name="沪深300连续",
            currency="CNY",
            product_type="continuous",
        ).product_type
        == "continuous"
    )


def test_unexpected_archive_failure_records_safe_code_and_context(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import qtrade_data_core.collection as module
    from qtrade_data_core.collection import CollectionService

    req = request()
    task = replace(part(req), attempt=1)
    failures, logs = [], []
    repository = SimpleNamespace(claim=lambda now: task, fail=lambda *args: failures.append(args))
    archive = SimpleNamespace(
        recover=lambda part: (_ for _ in ()).throw(OSError("private file details"))
    )
    auth = StaticScopeAuthorizer({f"{req.actor_id}:{req.scope_id}": "manage"})
    monkeypatch.setattr(module.logger, "warning", lambda message, **kwargs: logs.append(kwargs))
    assert CollectionService(auth, repository, archive, ()).run_once() == task
    assert failures[0][1] == "unavailable" and failures[0][2] is not None
    assert logs[0]["extra"]["stage"] == "recovery"
    assert logs[0]["extra"]["error_code"] == "unavailable"
    assert "private" not in json.dumps(logs)
    assert collection_context.get() is None


def test_worker_retains_bad_source_body_before_transform_without_publication(tmp_path):
    from types import SimpleNamespace

    from qtrade_data_contracts.collection import CollectionRawCapture

    bad = [{"t": "not-a-time", "o": "3.2", "h": "3.3", "l": "3.1", "c": "3.25", "v": "1"}]

    def respond(req):
        return httpx.Response(200, json=[20250901] if "/tcalendar/" in req.url.path else bad)

    provider = MaiRuiCollection(
        "fixture",
        pro=True,
        cache=tmp_path / "cache",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    task = replace(part(), attempt=1, lease_token=uuid4())
    failed, completed = [], []
    repo = SimpleNamespace(
        claim=lambda now: task,
        fail=lambda *args: failed.append(args),
        complete=lambda *args: completed.append(args),
    )
    archive = FileCollectionArchive(tmp_path / "archive")
    auth = StaticScopeAuthorizer({f"{task.request.actor_id}:{task.request.scope_id}": "manage"})
    assert CollectionService(auth, repo, archive, (provider,)).run_once() == task
    assert failed[0][1] == "unverified" and failed[0][2] is None and not completed
    directory = archive.root / str(task.request.scope_id) / str(task.part_id)
    receipts = [
        CollectionRawCapture.model_validate_json(p.read_bytes())
        for p in (directory / "source-captures").glob("receipt-*.json")
    ]
    history = next(receipt for receipt in receipts if receipt.kind == "history")
    raw = directory / "source-captures" / f"payload-{history.content_sha256}.json"
    assert json.loads(raw.read_bytes()) == bad
    assert history.response_rows == 1 and history.lease_token == task.lease_token
    assert not (directory / "manifest.json").exists() and archive.recover(task) is None
    failures = archive.processing_failures(task)
    assert len(failures) == 1 and failures[0].normalized_record_count is None
    assert (
        failures[0].status == "blocked"
        and failures[0].checker_version == "collection-normalization-check.v1"
    )
    assert set(failures[0].raw_capture_ids) == {r.capture_id for r in receipts}
    before_failure = next(directory.glob("processing-failure-*.json")).read_bytes()
    archive.record_processing_failure(task, failures[0])
    assert next(directory.glob("processing-failure-*.json")).read_bytes() == before_failure
    # Referenced bodies must still exist; an orphaned metadata receipt is not
    # evidence that the original rejection input was retained.
    orphan = replace(task, part_id=uuid4())
    receipt = archive.capture_raw(orphan, "history", bad)
    rejected = failures[0].model_copy(
        update={
            "failure_id": uuid4(),
            "part_id": orphan.part_id,
            "raw_capture_ids": (receipt.capture_id,),
        }
    )
    orphan_dir = archive.root / str(orphan.request.scope_id) / str(orphan.part_id)
    (orphan_dir / "source-captures" / f"payload-{receipt.content_sha256}.json").unlink()
    with pytest.raises(DataFailure, match="unavailable"):
        archive.record_processing_failure(orphan, rejected)
    before = sorted(p.read_bytes() for p in (directory / "source-captures").glob("receipt-*.json"))
    archive.capture_raw(task, "history", bad)
    assert before == sorted(
        p.read_bytes() for p in (directory / "source-captures").glob("receipt-*.json")
    )
    with pytest.raises(DataFailure, match="conflict"):
        archive.capture_raw(
            replace(task, instrument_name="unchanged", job_id=uuid4()), "history", bad
        )


@pytest.mark.parametrize(
    "status,body,code",
    [
        (401, b"private provider denied", "unauthorized"),
        (429, b"private provider throttled", "rate_limited"),
        (503, b"private temporary failure", "unavailable"),
        (200, b"{ malformed JSON \\xff", "unavailable"),
    ],
)
def test_http_body_retained_before_status_or_json_failure(tmp_path, caplog, status, body, code):
    from qtrade_data_contracts.collection import CollectionRawCapture

    task = replace(part(), attempt=1, lease_token=uuid4())
    archive = FileCollectionArchive(tmp_path / "archive")
    provider = MaiRuiCollection(
        "fixture",
        cache=tmp_path / "cache",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(status, content=body))
        ),
    )
    with caplog.at_level(logging.INFO), pytest.raises(DataFailure, match=code):
        provider._get(
            "fixture/history",
            capture=lambda kind, payload: archive.capture_raw(task, kind, payload),
        )
    directory = archive.root / str(task.request.scope_id) / str(task.part_id)
    receipt = CollectionRawCapture.model_validate_json(
        next((directory / "source-captures").glob("receipt-*.json")).read_bytes()
    )
    assert receipt.kind == "http_response" and receipt.response_rows is None
    envelope = json.loads(
        (directory / "source-captures" / f"payload-{receipt.content_sha256}.json").read_bytes()
    )
    assert envelope["http_status"] == status and envelope["complete"] is True
    assert b64decode(envelope["body_base64"], validate=True) == body
    assert "private" not in caplog.text and "body_base64" not in caplog.text
    assert archive.recover(task) is None and not (directory / "manifest.json").exists()


@pytest.mark.parametrize("body", [b"private incomplete prefix", b""])
def test_shared_transport_partial_capture_preserves_v1_archive_semantics(tmp_path, body):
    from qtrade_data_mairui.bounded_http import BoundedHttpClient, TransportFailure

    class Partial(BoundedHttpClient):
        def get(self, url, *, params=None, timeout=90):
            raise TransportFailure("timeout", response_status=200, response_body=body)

    task = replace(part(), attempt=1, lease_token=uuid4())
    archive = FileCollectionArchive(tmp_path / "archive")
    provider = MaiRuiCollection("fixture", cache=tmp_path / "cache")
    provider.client = Partial()
    with pytest.raises(DataFailure, match="timeout"):
        provider._get(
            "fixture/history",
            capture=lambda kind, payload: archive.capture_raw(task, kind, payload),
        )
    (receipt,) = archive.source_captures(task, datetime.now(UTC))
    assert receipt.kind == "http_response" and receipt.response_rows is None
    directory = archive.root / str(task.request.scope_id) / str(task.part_id)
    envelope = json.loads(
        (directory / "source-captures" / f"payload-{receipt.content_sha256}.json").read_bytes()
    )
    assert envelope["complete"] is False and envelope["http_status"] == 200
    assert b64decode(envelope["body_base64"], validate=True) == body
    assert archive.qualities(task) == () and archive.recover(task) is None


@pytest.mark.parametrize(
    "payload", [{"code": 200, "data": {"unexpected": "shape"}}, {"code": 200, "data": []}]
)
def test_factor_capture_precedes_shape_validation_and_empty_is_not_publication(tmp_path, payload):
    from qtrade_data_contracts.collection import CollectionRawCapture

    def respond(req):
        if "/factor/list/" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [{"factor_id": "pe_ttm", "name": "PE", "category": "valuation"}],
                },
            )
        return httpx.Response(200, json=payload)

    provider = MaiRuiCollection(
        "fixture",
        pro=True,
        cache=tmp_path / "cache",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    task = part(
        request(
            source="mairui.factor.history",
            dataset_kind="factors",
            fields=("pe_ttm",),
            granularity="1d",
        )
    )
    archive = FileCollectionArchive(tmp_path / "archive")

    def capture(kind, body):
        archive.capture_raw(task, kind, body)

    if isinstance(payload["data"], dict):
        with pytest.raises(DataFailure, match="unverified"):
            provider.fetch(task, capture=capture)
    else:
        assert provider.fetch(task, capture=capture).records == ()
    directory = archive.root / str(task.request.scope_id) / str(task.part_id) / "source-captures"
    receipt = CollectionRawCapture.model_validate_json(
        next(directory.glob("receipt-*.json")).read_bytes()
    )
    assert receipt.kind == "factor_history"
    assert receipt.response_rows == (0 if isinstance(payload["data"], list) else None)
    assert (
        json.loads((directory / f"payload-{receipt.content_sha256}.json").read_bytes()) == payload
    )
    assert archive.recover(task) is None


def test_north_catalog_presence_is_not_blanket_disabled_and_empty_history_is_not_denial(tmp_path):
    def response(req):
        if "/factor/list/" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [
                        {
                            "factor_id": "north_holding_ratio",
                            "name": "北向持股比例",
                            "category": "holding",
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"code": 200, "data": []})

    provider = MaiRuiCollection(
        "fixture-key",
        pro=True,
        cache=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(response)),
    )
    definition = provider.factors()[0]
    assert definition.available and definition.unavailable_reason is None
    assert definition.unit == "unknown" and definition.update_frequency == "unknown"
    result = provider.fetch(
        part(
            request(dataset_kind="factors", granularity="1d", fields=("north_holding_ratio",)),
        )
    )
    assert result.records == () and result.evidence["availability_evidence"] == "unknown"


def test_factor_oversized_range_and_unexpected_dates_rejected(tmp_path):
    calls = []

    def response(req):
        calls.append(req.url.path)
        if "/factor/list/" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [{"factor_id": "pe_ttm", "name": "PE", "category": "valuation"}],
                },
            )
        return httpx.Response(
            200, json={"code": 200, "data": [{"trade_date": "20150101", "value": 1}]}
        )

    provider = MaiRuiCollection(
        "fixture-key",
        pro=True,
        cache=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(response)),
    )
    task = part(request(dataset_kind="factors", granularity="1d", fields=("pe_ttm",)))
    with pytest.raises(DataFailure, match="invalid_input"):
        provider.fetch(
            replace(
                task, start=datetime(2024, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC)
            )
        )
    assert not calls
    with pytest.raises(DataFailure, match="unverified"):
        provider.fetch(task)


def test_one_minute_opening_observation_is_retained_and_not_inferred_as_auction(tmp_path):
    def response(req):
        if "/tcalendar/" in req.url.path:
            return httpx.Response(200, json=[20250901])
        return httpx.Response(
            200,
            json=[
                {"t": "2025-09-01 09:30:00", "o": 3, "h": 3, "l": 3, "c": 3, "v": 1, "a": 300},
                {"t": "2025-09-01 09:31:00", "o": 3, "h": 3, "l": 3, "c": 3, "v": 1, "a": 300},
            ],
        )

    provider = MaiRuiCollection(
        "fixture",
        pro=True,
        cache=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(response)),
    )
    result = provider.fetch(part(request(granularity="1m")))
    assert len(result.records) == len(result.raw) == 2
    assert result.records[0].timestamp == datetime(2025, 9, 1, 1, 29, tzinfo=UTC)
    assert result.evidence["opening_boundary_source_labels"] == ["2025-09-01 09:30:00"]
    assert result.evidence["opening_boundary_semantics"] == "unverified"
    assert any("不能认证为常规一分钟区间" in text for text in result.limitations)
