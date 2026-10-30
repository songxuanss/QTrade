"""Consumer validates the seam before any visible publication and retries safely."""

import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_independent_collection import START, bar, part

from qtrade_application.batches import RawBatchService
from qtrade_composition.collector import CollectorImporter
from qtrade_data_contracts.collection import CollectionRecordPage, digest
from qtrade_data_core.collection import CollectionResult
from qtrade_data_core.ports import DataFailure
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.batches import MockBatchRepository
from qtrade_mock.datasets import MockDatasetRepository
from qtrade_mock.ports import MockMarketData, MockObjectStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec


def setup(tmp_path, mutation=None, fail_market=False):
    task = part()
    bars = (bar(), bar().model_copy(update={"timestamp": START + timedelta(minutes=5)}))
    publication = FileCollectionArchive(tmp_path).publish(
        task,
        CollectionResult(({"c": "3.25"}, {"c": "3.25"}), bars, {"volume": "shares"}, "UTC", {}),
    )
    consumer = CollectorImporter.__new__(CollectorImporter)
    consumer.datasets, consumer.objects = MockDatasetRepository(), MockObjectStore()
    consumer.market = MockMarketData()
    consumer.batches = RawBatchService(
        MockBatchRepository(), consumer.objects, ParquetRawRecordCodec()
    )
    workspace = Workspace(task.request.scope_id, "Test", "organization", None, False, False, START)
    access = WorkspaceAccess(workspace, task.request.actor_id, Role.ADMIN)

    def request(method, path, **kwargs):
        offset = kwargs["params"]["offset"]
        records = (bars[offset],)
        page = CollectionRecordPage(
            schema_version=1,
            scope_id=publication.scope_id,
            publication_id=publication.publication_id,
            records=records,
            offset=offset,
            next_offset=offset + 1 if offset == 0 else None,
            page_sha256=digest([b.model_dump(mode="json") for b in records]),
        ).model_dump(mode="json")
        return mutation(page) if mutation else page

    consumer.client = SimpleNamespace(request=request)
    if fail_market:
        consumer.market.write_bars = lambda *args: (_ for _ in ()).throw(
            PortError(ErrorCode.UNAVAILABLE, "fixture")
        )
    return consumer, access, publication


def test_paged_import_idempotent_and_original_manifest_provenance(tmp_path):
    consumer, access, publication = setup(tmp_path)
    consumer.import_publication(access, publication)
    stored = consumer.datasets.get(publication.scope_id, publication.publication_id)
    assert stored.metadata.dataset_id == publication.publication_id
    assert stored.metadata.granularity == "5m"
    consumer.import_publication(access, publication)
    assert len(consumer.datasets.list(publication.scope_id)) == 1
    report = json.loads(
        consumer.objects.get(
            publication.scope_id,
            stored.metadata.quality_report_key,
            expected_sha256=stored.quality_report_sha256,
        )
    )
    assert report["collector_publication"]["raw_sha256"] == publication.raw_sha256
    assert report["coverage"]["research_readiness"] == "unverified"
    with pytest.raises(DataFailure, match="conflict"):
        consumer.import_publication(
            access, publication.model_copy(update={"instrument_name": "改名"})
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p | {"scope_id": str(uuid4())},
        lambda p: p | {"publication_id": str(uuid4())},
        lambda p: p | {"offset": 3},
        lambda p: p | {"next_offset": 3},
        lambda p: p | {"page_sha256": "0" * 64},
    ],
)
def test_invalid_pages_never_commit_visible_metadata(tmp_path, change):
    consumer, access, publication = setup(tmp_path, change)
    with pytest.raises((DataFailure, ValueError)):
        consumer.import_publication(access, publication)
    assert consumer.datasets.list(publication.scope_id) == ()


def test_failed_market_write_stays_invisible_then_retry(tmp_path):
    consumer, access, publication = setup(tmp_path, fail_market=True)
    with pytest.raises(PortError):
        consumer.import_publication(access, publication)
    assert consumer.datasets.list(publication.scope_id) == ()
    consumer.market = MockMarketData()
    consumer.import_publication(access, publication)
    assert len(consumer.datasets.list(publication.scope_id)) == 1


@pytest.mark.parametrize("update", [{"records_sha256": "0" * 64}, {"record_count": 3}])
def test_whole_publication_integrity_rejects_before_commit(tmp_path, update):
    consumer, access, publication = setup(tmp_path)
    with pytest.raises(DataFailure, match="unverified"):
        consumer.import_publication(access, publication.model_copy(update=update))
    assert consumer.datasets.list(publication.scope_id) == ()


def test_record_identity_range_and_order_validated(tmp_path):
    for update in (
        {"instrument_id": "600519.SH"},
        {"timestamp": "2024-01-01T00:00:00Z"},
        {"granularity": "30m"},
    ):

        def invalid(page, update=update):
            page["records"][0].update(update)
            page["page_sha256"] = digest(page["records"])
            return page

        consumer, access, publication = setup(tmp_path, invalid)
        with pytest.raises(DataFailure, match="unverified"):
            consumer.import_publication(access, publication)
        assert consumer.datasets.list(publication.scope_id) == ()
