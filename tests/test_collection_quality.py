"""Quality decisions block corrupted identity/range without inventing price coverage."""

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_independent_collection import START, bar, part, request

from qtrade_data_contracts.collection import FactorPoint
from qtrade_data_contracts.collection_quality import CollectionQualityResult
from qtrade_data_core.collection import CollectionResult, CollectionService
from qtrade_data_core.collection_quality import evaluate
from qtrade_data_core.ports import DataFailure
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_mairui.collection import MaiRuiCollection


def result(**changes):
    return replace(
        CollectionResult(
            ({"c": "3.25"},),
            (bar(),),
            {"prices": "CNY", "value": "CNY", "volume": "shares"},
            "UTC",
            {},
        ),
        **changes,
    )


@pytest.mark.parametrize(
    "changes,code",
    [
        (
            {"records": (bar().model_copy(update={"instrument_id": "600519.SH"}),)},
            "identity_mismatch",
        ),
        (
            {"records": (bar().model_copy(update={"timestamp": START - timedelta(seconds=1)}),)},
            "range_mismatch",
        ),
        ({"records": (bar(), bar())}, "duplicate"),
        ({"records": (bar().model_copy(update={"close": Decimal("NaN")}),)}, "record_invalid"),
        ({"units": {"prices": "CNY", "volume": "lots", "value": "CNY"}}, "unit_mismatch"),
    ],
)
def test_blocking_inputs(changes, code):
    quality = evaluate(part(), result(**changes), "Asia/Shanghai", START, source_captured=True)
    assert quality.status == "blocked"
    assert any(issue.code == code and issue.severity == "block" for issue in quality.issues)


def test_missing_dates_intraday_and_source_flags_are_explicit_warnings():
    data = result(
        records=(bar().model_copy(update={"quality": "suspect"}),),
        evidence={"expected_dates": ["2025-09-01", "2025-09-02"]},
    )
    quality = evaluate(part(), data, "Asia/Shanghai", START, source_captured=True)
    assert quality.status == "warning"
    assert {issue.code for issue in quality.issues} == {
        "source_suspect",
        "missing_trade_dates",
        "intraday_completeness_unknown",
    }
    assert len(data.records) == 1 and data.records[0].timestamp == START


def test_empty_response_not_price_coverage_and_capture_required():
    data = CollectionResult((), (), {}, "UTC", {})
    assert (
        evaluate(part(), data, "Asia/Shanghai", START, source_captured=True).status
        == "no_observations"
    )
    assert evaluate(part(), data, "Asia/Shanghai", START, source_captured=False).status == "blocked"


def test_factor_null_unknown_semantics_warn_and_wrong_identity_blocks():
    task = part(
        request(
            source="mairui.factor.history",
            dataset_kind="factors",
            granularity="1d",
            fields=("pe_ttm",),
        )
    )
    point = FactorPoint(
        instrument_id=task.instrument_id,
        factor_id="pe_ttm",
        trade_date=(START + timedelta(days=1)).date(),
        value=None,
        value_kind="missing",
        missing_reason="provider_null",
        calc_version="unknown",
    )
    data = CollectionResult(
        ({"value": None},), (point,), {"value": "unknown"}, "local_trade_date", {}
    )
    quality = evaluate(task, data, "Asia/Shanghai", START, source_captured=True)
    assert quality.status == "warning"
    assert {issue.code for issue in quality.issues} == {
        "factor_null",
        "knowledge_unknown",
        "factor_unit_unknown",
        "factor_definition_unknown",
    }
    assert (
        evaluate(
            task,
            replace(data, records=(point.model_copy(update={"factor_id": "roe_ttm"}),)),
            "Asia/Shanghai",
            START,
            source_captured=True,
        ).status
        == "blocked"
    )


def test_daily_complete_calendar_passes_and_outside_calendar_blocks():
    task = part(request(granularity="1d"))
    data = result(
        records=(bar().model_copy(update={"granularity": "1d"}),),
        evidence={"expected_dates": ["2025-09-01"]},
    )
    quality = evaluate(task, data, "Asia/Shanghai", START, source_captured=True)
    assert quality.status == "passed" and not quality.issues
    bad = replace(data, evidence={"expected_dates": ["2025-09-18"]})
    rejected = evaluate(task, bad, "Asia/Shanghai", START, source_captured=True)
    assert rejected.status == "blocked"
    assert any(issue.code == "calendar_invalid" for issue in rejected.issues)


def test_worker_blocks_publication_and_keeps_raw_quality_decision(tmp_path):
    task = replace(part(), attempt=1, lease_token=uuid4())
    provider = MaiRuiCollection("fixture", pro=True, cache=tmp_path / "cache")

    def fetched(part, *, capture):
        capture("history", [{"c": "3.25"}])
        return result(records=(bar().model_copy(update={"instrument_id": "600519.SH"}),))

    # This test isolates the legacy provider quality seam, not prepared capture.
    provider = SimpleNamespace(datasets=provider.datasets, fetch=fetched)
    failed, completed = [], []
    repo = SimpleNamespace(
        claim=lambda now: task,
        fail=lambda *args: failed.append(args),
        complete=lambda *args: completed.append(args),
    )
    archive = FileCollectionArchive(tmp_path / "archive")
    auth = StaticScopeAuthorizer({f"{task.request.actor_id}:{task.request.scope_id}": "manage"})
    CollectionService(auth, repo, archive, (provider,)).run_once()
    assert failed[0][1] == "unverified" and not completed
    directory = archive.root / str(task.request.scope_id) / str(task.part_id)
    quality = CollectionQualityResult.model_validate_json(
        next(directory.glob("quality-*.json")).read_bytes()
    )
    assert quality.status == "blocked" and quality.rule_version == "collection-quality.v1"
    assert list((directory / "source-captures").glob("receipt-*.json"))
    assert archive.recover(task) is None and not (directory / "manifest.json").exists()
    later = evaluate(
        task,
        result(records=(bar().model_copy(update={"instrument_id": "600519.SH"}),)),
        "Asia/Shanghai",
        START + timedelta(days=1),
        source_captured=True,
    )
    assert later.quality_id == quality.quality_id
    before = next(directory.glob("quality-*.json")).read_bytes()
    archive.record_quality(task, later)
    assert next(directory.glob("quality-*.json")).read_bytes() == before
    assert archive.qualities(task) == (quality,)
    missing_id = uuid4()
    assert archive.qualities(replace(task, part_id=missing_id)) == ()
    assert not (archive.root / str(task.request.scope_id) / str(missing_id)).exists()


@pytest.mark.parametrize("version", [True, "1", 2, None])
def test_quality_wire_version_requires_explicit_integer_one(version):
    quality = evaluate(part(), result(), "Asia/Shanghai", START, source_captured=True)
    body = quality.model_dump(mode="json")
    if version is None:
        body.pop("schema_version")
    else:
        body["schema_version"] = version
    with pytest.raises(ValidationError):
        CollectionQualityResult.model_validate_json(json.dumps(body))


def test_corrupt_repository_scope_never_opens_foreign_archive():
    task = part()
    other = replace(task, request=task.request.model_copy(update={"scope_id": uuid4()}))
    auth = StaticScopeAuthorizer({f"{task.request.actor_id}:{task.request.scope_id}": "read"})
    reads = []
    repo = SimpleNamespace(part_for_job=lambda *args: other)
    archive = SimpleNamespace(qualities=lambda candidate: reads.append(candidate))
    with pytest.raises(DataFailure, match="unavailable"):
        CollectionService(auth, repo, archive, ()).qualities(
            task.request.actor_id, task.request.scope_id, task.job_id, task.part_id
        )
    assert not reads
