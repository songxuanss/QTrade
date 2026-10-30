"""Replay lineage, provenance, failure recovery and historical reference preservation."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from qtrade_application.batches import RawBatchService
from qtrade_application.datasets import DatasetReplayService
from qtrade_contracts import BacktestRun, Bar
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.bar_json import StandardBarJsonTransformation
from qtrade_domain.batches import BatchInput
from qtrade_domain.datasets import ReplayRequest
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import BarQuery, ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord
from qtrade_mock.batches import MockBatchRepository
from qtrade_mock.datasets import MockDatasetRepository
from qtrade_mock.ports import MockMarketData, MockObjectStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec


class RevisedTransformation(StandardBarJsonTransformation):
    version = "test.revised-bar-json.v2"

    def transform(self, dataset_id, batches):
        return tuple(
            bar.model_copy(update={"close": Decimal("10.75"), "quality": "suspect"})
            for bar in super().transform(dataset_id, batches)
        )


@pytest.fixture
def data():
    workspace = Workspace(uuid4(), "Replay", "organization", None, False, False, datetime.now(UTC))
    access = WorkspaceAccess(workspace, uuid4(), Role.ADMIN)
    bar = Bar.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Bar.json").read_text()
    ).model_copy(update={"workspace_id": workspace.workspace_id, "batch_id": uuid4()})
    draft = BatchInput(
        workspace.workspace_id,
        bar.batch_id,
        "qtrade.standard-bar-json",
        "test-license",
        uuid4(),
        bar.instrument_id,
        "1m",
        datetime.now(UTC),
        (RawRecord(0, bar.model_dump_json().encode(), "application/json"),),
    )
    objects, repository, market = MockObjectStore(), MockDatasetRepository(), MockMarketData()
    batches = RawBatchService(MockBatchRepository(), objects, ParquetRawRecordCodec())
    batches.publish(access, draft)
    transforms = [StandardBarJsonTransformation(), RevisedTransformation()]
    service = DatasetReplayService(repository, batches, market, objects, transforms)
    request = ReplayRequest(
        workspace.workspace_id,
        uuid4(),
        (bar.batch_id,),
        transforms[0].version,
        bar.timestamp,
        bar.timestamp + timedelta(minutes=1),
        datetime.now(UTC),
    )
    return access, bar, draft, objects, repository, market, batches, service, request


def query(request, bar):
    return BarQuery(
        request.workspace_id,
        request.dataset_id,
        bar.instrument_id,
        "1m",
        request.start,
        request.end,
    )


def test_revised_rules_create_new_dataset_and_preserve_raw_old_bars_and_backtest_reference(data):
    access, bar, draft, objects, repository, market, batches, service, request = data
    original_raw = batches.read(access, draft.batch_id)
    first = service.replay(access, request)
    old_bars = market.query_bars(query(request, bar)).bars
    historical_run = BacktestRun.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/BacktestRun.json").read_text()
    ).model_copy(update={"workspace_id": request.workspace_id, "dataset_id": request.dataset_id})
    revised = replace(
        request,
        dataset_id=uuid4(),
        parent_dataset_id=request.dataset_id,
        transformation_version=RevisedTransformation.version,
    )
    second = service.replay(access, revised)
    assert second.metadata.version == 2 and second.parent_dataset_id == first.metadata.dataset_id
    assert second.metadata.content_sha256 != first.metadata.content_sha256
    assert market.query_bars(query(revised, bar)).bars[0].close == Decimal("10.75")
    assert market.query_bars(query(request, bar)).bars == old_bars
    assert repository.get(request.workspace_id, request.dataset_id) == first
    assert batches.read(access, draft.batch_id) == original_raw
    assert historical_run.dataset_id == first.metadata.dataset_id
    report = json.loads(
        objects.get(
            request.workspace_id,
            second.metadata.quality_report_key,
            expected_sha256=second.quality_report_sha256,
        )
    )
    assert report["quality_counts"] == {"valid": 0, "suspect": 1, "quarantined": 0}
    assert report["source_batches"][0]["content_sha256"] == original_raw.metadata.content_sha256
    assert report["checks"] == [
        "bar-contract",
        "source-provenance",
        "unique-bar",
        "requested-range",
    ]


def test_exact_concurrent_retries_and_identity_conflicts(data):
    access, bar, _, _, repository, market, _, service, request = data
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.replay(access, request), range(8)))
    assert all(result == results[0] for result in results)
    assert len(repository._datasets) == len(market._datasets) == 1
    with pytest.raises(PortError) as conflict:
        service.replay(
            access, replace(request, transformation_version=RevisedTransformation.version)
        )
    assert conflict.value.code == ErrorCode.CONFLICT
    assert market.query_bars(query(request, bar)).bars[0].close == bar.close
    # Committed retries do not require an old transformation implementation to remain loaded.
    _, _, _, objects, _, _, batches, _, _ = data
    reconstructed = DatasetReplayService(repository, batches, market, objects, [])
    assert reconstructed.replay(access, request) == results[0]


def test_metadata_failure_leaves_reusable_candidates_but_no_committed_dataset(data):
    access, _, _, objects, repository, market, batches, _, request = data

    class FailOnce(MockDatasetRepository):
        failed = False

        def put(self, actor, dataset):
            if not self.failed:
                self.failed = True
                raise PortError(ErrorCode.UNAVAILABLE, "test metadata failure")
            super().put(actor, dataset)

    repository = FailOnce()
    service = DatasetReplayService(
        repository, batches, market, objects, [StandardBarJsonTransformation()]
    )
    with pytest.raises(PortError) as failed:
        service.replay(access, request)
    assert failed.value.code == ErrorCode.UNAVAILABLE and not repository._datasets
    assert len(market._datasets) == 1
    candidates = dict(objects._objects)
    published = service.replay(access, request)
    assert dict(objects._objects) == candidates
    assert service.read(access, request.dataset_id) == published


def test_authorization_scope_unknown_rules_and_missing_parent_reject_before_publication(data):
    access, _, _, objects, repository, market, _, service, request = data
    before = dict(objects._objects)
    for actor, changed, code in (
        (replace(access, role=Role.VIEWER), request, ErrorCode.REJECTED),
        (access, replace(request, workspace_id=uuid4()), ErrorCode.NOT_FOUND),
        (access, replace(request, transformation_version="unknown"), ErrorCode.INVALID_INPUT),
        (access, replace(request, parent_dataset_id=uuid4()), ErrorCode.NOT_FOUND),
        (access, replace(request, batch_ids=(uuid4(),)), ErrorCode.NOT_FOUND),
    ):
        with pytest.raises(PortError) as rejected:
            service.replay(actor, changed)
        assert rejected.value.code == code
    assert dict(objects._objects) == before and not repository._datasets and not market._datasets
    first = service.replay(access, request)
    assert service.read(replace(access, role=Role.VIEWER), request.dataset_id) == first
    with pytest.raises(PortError) as lineage:
        service.replay(
            access,
            replace(
                request,
                dataset_id=uuid4(),
                parent_dataset_id=request.dataset_id,
                batch_ids=(uuid4(),),
            ),
        )
    assert lineage.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize("failure", ["scope", "provenance", "duplicate", "end", "contract"])
def test_invalid_transformations_never_publish_a_dataset(data, failure):
    access, _, _, objects, repository, market, batches, _, request = data

    class Invalid(StandardBarJsonTransformation):
        def transform(self, dataset_id, source_batches):
            bars = super().transform(dataset_id, source_batches)
            update = {
                "scope": {"workspace_id": uuid4()},
                "provenance": {"batch_id": uuid4()},
                "end": {"timestamp": request.end},
                "contract": {"close": Decimal("999")},
            }
            return (
                bars + bars
                if failure == "duplicate"
                else (bars[0].model_copy(update=update[failure]),)
            )

    service = DatasetReplayService(repository, batches, market, objects, [Invalid()])
    before = dict(objects._objects)
    with pytest.raises(PortError) as invalid:
        service.replay(access, request)
    assert invalid.value.code == ErrorCode.INVALID_INPUT
    assert dict(objects._objects) == before and not repository._datasets and not market._datasets


def test_report_corruption_prevents_committed_reads_and_retries(data):
    access, _, _, objects, _, _, _, service, request = data
    result = service.replay(access, request)
    original = objects._objects[(request.workspace_id, result.metadata.quality_report_key)]
    objects._objects[(request.workspace_id, result.metadata.quality_report_key)] = (
        original[0],
        b"corrupt",
    )
    for operation in (
        lambda: service.read(access, request.dataset_id),
        lambda: service.replay(access, request),
    ):
        with pytest.raises(PortError) as corrupt:
            operation()
        assert corrupt.value.code == ErrorCode.CHECKSUM_MISMATCH


def test_replay_request_normalizes_dst_fold_range_and_equivalent_offsets(data):
    request = data[-1]
    zone = ZoneInfo("America/New_York")
    folded = replace(
        request,
        start=datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0),
        end=datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1),
    )
    assert folded.end - folded.start == timedelta(hours=1)
    assert (
        replace(
            folded, start=folded.start.astimezone(zone), end=folded.end.astimezone(zone)
        ).fingerprint
        == folded.fingerprint
    )
    with pytest.raises(PortError) as invalid:
        replace(folded, start=folded.end, end=folded.start)
    assert invalid.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize("failure", ["malformed", "mime", "source", "provenance", "tick"])
def test_preserved_raw_records_reject_unsupported_format_without_partial_replay(data, failure):
    access, bar, draft, objects, repository, market, batches, service, request = data
    batch_id = uuid4()
    raw_bar = bar.model_copy(update={"batch_id": batch_id})
    record = RawRecord(0, raw_bar.model_dump_json().encode(), "application/json")
    changes = {
        "malformed": {"records": (RawRecord(0, b"{broken", "application/json"),)},
        "mime": {"records": (RawRecord(0, record.payload, "text/plain"),)},
        "source": {"source": "other-provider"},
        "provenance": {"instrument_id": "OTHER:ABC"},
        "tick": {"granularity": "tick"},
    }
    changed = (
        replace(draft, batch_id=batch_id, records=(record,), **changes[failure])
        if failure not in ("malformed", "mime")
        else replace(draft, batch_id=batch_id, **changes[failure])
    )
    batches.publish(access, changed)
    before = dict(objects._objects)
    with pytest.raises(PortError) as invalid:
        service.replay(access, replace(request, batch_ids=(batch_id,)))
    assert invalid.value.code == ErrorCode.INVALID_INPUT
    assert batches.read(access, batch_id).records == changed.records
    assert dict(objects._objects) == before and not repository._datasets and not market._datasets


def test_quality_assessment_is_frozen_and_readable_without_reassessing(data):
    access, _, _, objects, repository, market, batches, _, request = data

    class Assessment:
        transformation_version = StandardBarJsonTransformation.version
        calls = 0

        def assess(self, request, batches, bars):
            self.calls += 1
            return {"research_readiness": "incomplete", "missing_trading_days": ["2026-09-01"]}

    assessment = Assessment()
    service = DatasetReplayService(
        repository, batches, market, objects, [StandardBarJsonTransformation()], [assessment]
    )
    service.replay(access, request)
    report = service.quality_report(replace(access, role=Role.VIEWER), request.dataset_id)
    assert report["coverage"]["research_readiness"] == "incomplete"
    assert report["coverage"]["missing_trading_days"] == ["2026-09-01"]
    service.replay(access, request)
    assert assessment.calls == 1
    with pytest.raises(PortError) as scope:
        service.quality_report(
            replace(access, workspace=replace(access.workspace, workspace_id=uuid4())),
            request.dataset_id,
        )
    assert scope.value.code == ErrorCode.NOT_FOUND


def test_failed_quality_assessment_does_not_publish_market_or_metadata(data):
    access, _, _, objects, repository, market, batches, _, request = data

    class Reject:
        transformation_version = StandardBarJsonTransformation.version

        def assess(self, request, batches, bars):
            raise PortError(ErrorCode.INVALID_INPUT, "calendar evidence unavailable")

    before = dict(objects._objects)
    service = DatasetReplayService(
        repository, batches, market, objects, [StandardBarJsonTransformation()], [Reject()]
    )
    with pytest.raises(PortError) as rejected:
        service.replay(access, request)
    assert rejected.value.code == ErrorCode.INVALID_INPUT
    assert not repository._datasets and not market._datasets
    assert dict(objects._objects) == before
