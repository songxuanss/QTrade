from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from qtrade_application.batches import RawBatchService
from qtrade_application.datasets import DatasetReplayService
from qtrade_application.lineage import DataLineageService
from qtrade_application.market_data import MarketDataService
from qtrade_contracts import Bar
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.bar_json import StandardBarJsonTransformation
from qtrade_domain.datasets import ReplayRequest
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ingestion import CollectionRequest, ProviderPage, StagedPage
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.quality import QualityIssue
from qtrade_domain.raw_records import RawRecord
from qtrade_mock.batches import MockBatchRepository
from qtrade_mock.datasets import MockDatasetRepository
from qtrade_mock.ingestion import MockIngestionRepository
from qtrade_mock.ports import MockMarketData, MockObjectStore
from qtrade_mock.quality import MockQualityIssueStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec


@pytest.fixture
def lineage():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    workspace = Workspace(uuid4(), "Lineage", "organization", None, False, False, now)
    access = WorkspaceAccess(workspace, uuid4(), Role.ADMIN)
    raw_bar = Bar.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Bar.json").read_text()
    ).model_copy(update={"workspace_id": workspace.workspace_id})
    request = CollectionRequest(
        workspace.workspace_id,
        uuid4(),
        "qtrade.standard-bar-json",
        "license-reference",
        raw_bar.instrument_id,
        "1m",
        raw_bar.timestamp,
        raw_bar.timestamp + timedelta(minutes=1),
        now,
        StandardBarJsonTransformation.version,
    )
    record = RawRecord(0, raw_bar.model_dump_json().encode(), "application/json")
    objects = MockObjectStore()
    batches = RawBatchService(MockBatchRepository(), objects, ParquetRawRecordCodec())

    def task_access(actor, scope):
        return access if actor == access.user_id and scope == workspace.workspace_id else None

    tasks = MockIngestionRepository(task_access)
    tasks.create(access.user_id, request)
    lease = tasks.claim(
        access.user_id,
        uuid4(),
        workspace.workspace_id,
        request.task_id,
        now,
        now + timedelta(minutes=1),
    )
    assert lease is not None
    tasks.stage(lease, StagedPage(ProviderPage((record,), None), now), now)
    batch = batches.publish(access, request.batch(0, now, (record,)))
    task = tasks.checkpoint(lease, batch.batch_id, now)
    datasets, market = MockDatasetRepository(), MockMarketData()
    replay = DatasetReplayService(
        datasets,
        batches,
        market,
        objects,
        (StandardBarJsonTransformation(),),
    )
    published = replay.replay(
        access,
        ReplayRequest(
            workspace.workspace_id,
            request.dataset_id,
            task.batch_ids,
            request.transformation_version,
            request.start,
            request.end,
            now,
        ),
    )
    lease = tasks.claim(
        access.user_id,
        uuid4(),
        workspace.workspace_id,
        request.task_id,
        now + timedelta(seconds=1),
        now + timedelta(minutes=1),
    )
    assert lease is not None
    tasks.finish(lease, published.metadata.dataset_id, now + timedelta(seconds=1))
    issue_store = MockQualityIssueStore(task_access)
    issue = QualityIssue(
        uuid4(),
        workspace.workspace_id,
        request.dataset_id,
        "price_jump",
        "critical",
        raw_bar.instrument_id,
        "1m",
        raw_bar.timestamp,
        raw_bar.timestamp + timedelta(minutes=1),
        (batch.batch_id,),
        '{"threshold":"0.2"}',
        now,
    )
    issue_store.record_scan(access.user_id, (issue,))
    service = DataLineageService(
        replay,
        batches,
        MarketDataService(market),
        tasks,
        issue_store,
    )
    return access, raw_bar, published, batch, task, issue, service


def test_standard_bar_lineage_resolves_dataset_source_task_and_quality(lineage):
    access, bar, published, batch, task, issue, service = lineage
    item = service.for_bar(
        access,
        published.metadata.dataset_id,
        bar.instrument_id,
        bar.granularity,
        bar.timestamp,
    )
    assert item.bar.batch_id == batch.batch_id
    assert item.dataset.metadata.transformation_version == StandardBarJsonTransformation.version
    assert item.batch.content_sha256 == batch.content_sha256
    assert item.batch.ingestion_task_id == task.request.task_id
    assert item.collection.status == "completed"
    assert item.quality_issues == (issue,)


def test_lineage_is_workspace_scoped_and_requires_read(lineage):
    access, bar, published, _, _, _, service = lineage
    viewer = WorkspaceAccess(access.workspace, uuid4(), Role.VIEWER)
    assert (
        service.for_bar(
            viewer,
            published.metadata.dataset_id,
            bar.instrument_id,
            bar.granularity,
            bar.timestamp,
        ).dataset.metadata.dataset_id
        == published.metadata.dataset_id
    )
    with pytest.raises(PortError) as missing:
        service.for_bar(
            access,
            uuid4(),
            bar.instrument_id,
            bar.granularity,
            bar.timestamp,
        )
    assert missing.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(PortError) as invalid:
        service.for_bar(
            viewer,
            published.metadata.dataset_id,
            bar.instrument_id,
            bar.granularity,
            bar.timestamp.replace(tzinfo=None),
        )
    assert invalid.value.code == ErrorCode.INVALID_INPUT
