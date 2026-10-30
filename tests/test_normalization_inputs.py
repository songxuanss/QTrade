"""Controlled acquisition/RAW/checkpoint/pure conversion; not supplier readiness."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_independent_collection import START, part, request

from qtrade_data_contracts.normalization_input import (
    BarNormalizationInput,
    CollectionNormalizationInput,
)
from qtrade_data_core.collection_quality import evaluate
from qtrade_data_core.ports import DataFailure
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_files.normalization_inputs import FileNormalizationInputs
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_mairui.normalization import normalize


def row(t="2025-09-01 09:35:00"):
    return dict(t=t, o=10, h=11, l=9, c=10, v=2, a=2000, sf=0)


def setup(tmp_path, *, factors=False, rows=None):
    calls = []

    def respond(req):
        calls.append(req.url.path.split("/")[-2])
        if "/factor/list/" in req.url.path:
            return httpx.Response(
                200, json={"data": [{"factor_id": "pe_ttm", "name": "PE", "category": "valuation"}]}
            )
        if "/factor/history/" in req.url.path:
            return httpx.Response(
                200,
                text='[{"trade_date":"20250901","value":1.234567890123456789},{"trade_date":"20250902","value":"1.234567890123456789"},{"trade_date":"20250903","value":null}]',
            )
        if "tcalendar" in req.url.path:
            return httpx.Response(200, json=["20250901", "20250902"])
        return httpx.Response(200, json=rows if rows is not None else [row()])

    provider = MaiRuiCollection(
        "fixture",
        pro=True,
        cache=tmp_path / "cache",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    task = replace(part(), attempt=1, lease_token=uuid4())
    if factors:
        task = replace(
            part(
                request(
                    source="mairui.factor.history",
                    dataset_kind="factors",
                    granularity="1d",
                    fields=("pe_ttm",),
                    start=START - timedelta(hours=8),
                    end=START + timedelta(days=5) - timedelta(hours=8),
                )
            ),
            mapping_version="mairui.factor-history.v1",
            attempt=1,
            lease_token=uuid4(),
        )
    archive = FileCollectionArchive(tmp_path / "archive")
    captures, prepared = [], []

    def capture(kind, payload):
        captures.append(archive.capture_raw(task, kind, payload))

    def prepare(payload):
        prepared.append(archive.capture_prepared(task, payload, tuple(captures)))

    return provider, task, archive, captures, prepared, capture, prepare, calls


def fail_network(*args, **kwargs):
    raise AssertionError("pure normalization attempted acquisition")


def test_blocked_mapper_retains_exact_input_and_fixed_conversion_preserves_old_bytes(tmp_path):
    provider, task, archive, captures, prepared, capture, prepare, calls = setup(tmp_path)
    original = provider.normalize
    provider.normalize = lambda *args: (_ for _ in ()).throw(DataFailure("unverified"))
    with pytest.raises(DataFailure, match="unverified"):
        provider.fetch_prepared(task, capture=capture, prepared=prepare)
    assert len(prepared) == 1 and captures
    value = archive.normalization_inputs(task)[0]
    assert value == prepared[0] and archive.recover(task) is None
    frozen = {p: p.read_bytes() for p in archive.root.rglob("*") if p.is_file()}
    count = len(calls)
    provider.normalize = original
    provider._get = provider.factors = provider._calendar = provider._full_year = fail_network
    provider.fetch = fail_network
    req = task.request.model_copy(update={"request_id": uuid4()})
    target = replace(task, request=req, job_id=req.request_id, part_id=uuid4())
    result = provider.normalize(target, value.payload)
    quality = evaluate(target, result, "Asia/Shanghai", datetime.now(UTC), source_captured=True)
    assert quality.status == "warning" and result.records[0].volume == Decimal(200)
    archive.record_quality(target, quality)
    publication = archive.publish(target, result)
    assert archive.records(publication) == result.records
    assert archive.raw_records(publication) == result.raw
    assert publication.part_id != task.part_id and len(calls) == count
    assert all(p.read_bytes() == data for p, data in frozen.items())
    assert archive.recover(task) is None
    assert archive.normalization_input(task, value.input_id) == value
    assert all(p.stat().st_mode & 0o077 == 0 for p in archive.root.rglob("*") if p.is_file())


def test_factor_checkpoint_keeps_exact_numeric_signal_null_and_original_definition(tmp_path):
    provider, task, archive, _, prepared, capture, prepare, calls = setup(tmp_path, factors=True)
    original = provider.fetch_prepared(task, capture=capture, prepared=prepare)
    value = archive.normalization_inputs(task)[0]
    assert value.payload.value_kinds == ("number", "signal", "missing")
    assert value.payload.definition.unit == "unknown"
    assert value.payload.definition.calc_version == "unknown"
    count = len(calls)
    provider._get = provider.factors = provider.fetch = fail_network
    loaded = CollectionNormalizationInput.model_validate_json(value.model_dump_json())
    result = provider.normalize(task, loaded.payload)
    assert result == original and len(calls) == count
    assert result.records[0].value == Decimal("1.234567890123456789")
    assert result.records[0].value_kind == "number"
    assert (
        result.records[1].value == "1.234567890123456789"
        and result.records[1].value_kind == "signal"
    )
    assert result.records[2].value is None and result.records[2].missing_reason == "provider_null"
    assert all(point.available_at is None for point in result.records)
    assert prepared == [value]


def test_invalid_source_timestamp_is_frozen_before_selection_rejects(tmp_path):
    provider, task, archive, _, prepared, capture, prepare, _ = setup(
        tmp_path, rows=[row("invalid")]
    )
    with pytest.raises(DataFailure, match="unverified"):
        provider.fetch_prepared(task, capture=capture, prepared=prepare)
    assert len(prepared) == 1
    value = archive.normalization_inputs(task)[0]
    assert value.payload.slices[0].rows[0]["t"] == "invalid"
    assert archive.recover(task) is None


def test_cross_year_acquisition_slices_preserve_original_cropping(tmp_path):
    first = datetime(2025, 12, 30, 16, tzinfo=UTC)
    middle = first + timedelta(days=1)
    last = middle + timedelta(days=1)
    task = part(request(source="mairui.history.bars", granularity="1d", start=first, end=last))
    both = [row("2025-12-31 00:00:00"), row("2026-01-01 00:00:00")]
    payload = BarNormalizationInput.model_validate_json(
        json.dumps(
            dict(
                kind="bars",
                slices=[
                    dict(start=first.isoformat(), end=middle.isoformat(), rows=both),
                    dict(start=middle.isoformat(), end=last.isoformat(), rows=both),
                ],
                calendar_days=["2025-12-31", "2026-01-01"],
                fetch_mechanisms=["regular_history"],
            )
        )
    )
    result = normalize(task, payload)
    assert len(result.records) == len(result.raw) == 2
    assert len({r.timestamp for r in result.records}) == 2


def test_storage_failure_prevents_normalization_and_no_read_side_effects(tmp_path):
    provider, task, archive, _, _, capture, _, _ = setup(tmp_path)
    converted = []
    provider.normalize = lambda *args: converted.append(args)

    def rejected(payload):
        raise DataFailure("unavailable")

    with pytest.raises(DataFailure, match="unavailable"):
        provider.fetch_prepared(task, capture=capture, prepared=rejected)
    assert converted == [] and archive.normalization_inputs(task) == ()
    directory = (
        archive.root / str(task.request.scope_id) / str(task.part_id) / "normalization-inputs"
    )
    assert not directory.exists()
    with pytest.raises(DataFailure, match="not_found"):
        archive.normalization_input(task, uuid4())
    assert not directory.exists()


def test_checkpoint_tamper_cross_part_and_receipt_corruption_fail_closed(tmp_path):
    provider, task, archive, captures, prepared, capture, prepare, _ = setup(tmp_path)
    provider.fetch_prepared(task, capture=capture, prepared=prepare)
    value = prepared[0]
    assert archive.capture_prepared(task, value.payload, tuple(captures)) == value
    with pytest.raises(DataFailure, match="unverified"):
        archive.capture_prepared(replace(task, lease_token=uuid4()), value.payload, tuple(captures))
    directory = archive.root / str(task.request.scope_id) / str(task.part_id)
    path = directory / "normalization-inputs" / f"input-{value.input_id}.json"
    before = path.read_bytes()
    doc = json.loads(before)
    doc["prepared_at"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(doc))
    with pytest.raises(DataFailure, match="unverified"):
        archive.normalization_input(task, value.input_id)
    path.write_bytes(before)
    receipt = captures[-1]
    (directory / "source-captures" / f"payload-{receipt.content_sha256}.json").write_bytes(
        b"changed"
    )
    with pytest.raises(DataFailure, match="unverified"):
        archive.capture_prepared(task, value.payload, tuple(captures))
    empty = FileNormalizationInputs(Path(tmp_path / "absent"), archive._write)
    assert empty.inputs(task) == () and not empty.root.exists()


@pytest.mark.parametrize("text", ["1.0000", "-0.0000", "1234", "1e2"])
def test_signal_text_keeps_lexical_value_across_existing_v1_wire(text):
    from qtrade_data_contracts.collection import FactorPoint

    point = FactorPoint(
        instrument_id="601288.SH",
        factor_id="fixture_signal",
        trade_date=START.date(),
        value=text,
        value_kind="signal",
        calc_version="fixture.v1",
    )
    assert point.value == text
    assert FactorPoint.model_validate_json(point.model_dump_json()).value == text


@pytest.mark.parametrize("text", ["1e2", "0.1234567890123456789", "9" * 39])
def test_numeric_text_still_enforces_original_decimal_limits(text):
    from pydantic import ValidationError

    from qtrade_data_contracts.collection import FactorPoint

    with pytest.raises(ValidationError):
        FactorPoint(
            instrument_id="601288.SH",
            factor_id="pe_ttm",
            trade_date=START.date(),
            value=text,
            value_kind="number",
            calc_version="unknown",
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_json_numeric_values_cannot_turn_into_archived_nulls(value):
    from pydantic import ValidationError

    from qtrade_data_contracts.normalization_input import BarSourceSlice

    body = dict(
        start=START.isoformat(), end=(START + timedelta(days=1)).isoformat(), rows=[{"o": value}]
    )
    with pytest.raises(ValidationError):
        BarSourceSlice.model_validate_json(json.dumps(body))
