"""Paged collection, retries, task leases and durable archive checkpoints."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from qtrade_application.batches import RawBatchService
from qtrade_application.datasets import DatasetReplayService
from qtrade_application.ingestion import HistoricalIngestionWorker
from qtrade_contracts import Bar
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.bar_json import StandardBarJsonTransformation
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ingestion import CollectionPolicy, CollectionRequest, ProviderPage, StagedPage
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord
from qtrade_mock.batches import MockBatchRepository
from qtrade_mock.datasets import MockDatasetRepository
from qtrade_mock.ingestion import MockIngestionRepository
from qtrade_mock.ports import MockMarketData, MockObjectStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec


@pytest.fixture
def setup():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    workspace = Workspace(uuid4(), "History", "organization", None, False, False, now)
    access = WorkspaceAccess(workspace, uuid4(), Role.ADMIN)
    grants = {access.user_id: access}

    def accesses(actor, wid):
        return grants.get(actor) if wid == workspace.workspace_id else None

    repository = MockIngestionRepository(accesses)
    objects = MockObjectStore()
    raw = RawBatchService(MockBatchRepository(), objects, ParquetRawRecordCodec())
    datasets = MockDatasetRepository()
    market = MockMarketData()
    transforms = [StandardBarJsonTransformation()]
    replay = DatasetReplayService(datasets, raw, market, objects, transforms)
    bars = [
        Bar.model_validate_json(
            (Path(__file__).parent / "fixtures/contracts/Bar.json").read_text()
        ).model_copy(
            update={
                "workspace_id": workspace.workspace_id,
                "batch_id": uuid4(),
                "timestamp": now + timedelta(minutes=index),
            }
        )
        for index in range(2)
    ]
    records = [
        RawRecord(index, bar.model_dump_json().encode(), "application/json")
        for index, bar in enumerate(bars)
    ]
    return (
        now,
        workspace,
        access,
        grants,
        repository,
        objects,
        raw,
        datasets,
        market,
        replay,
        bars,
        records,
    )


class ScriptedProvider:
    source = "qtrade.standard-bar-json"

    def __init__(self, pages):
        self.pages = list(pages)
        self.cursors = []

    def fetch(self, request, cursor):
        self.cursors.append(cursor)
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def worker(setup, provider, policy=None, batch_service=None):
    now, workspace, _, _, repository, objects, raw, _, market, replay, _, _ = setup
    return HistoricalIngestionWorker(
        repository, batch_service or raw, replay, (provider,), policy or CollectionPolicy()
    )


def request(setup, *, task_id=None, start=None, end=None, page_size=10):
    now, workspace = setup[:2]
    return CollectionRequest(
        workspace.workspace_id,
        task_id or uuid4(),
        "qtrade.standard-bar-json",
        "fixture-license",
        "MOCK:ABC",
        "1m",
        start or now,
        end or now + timedelta(hours=1),
        now,
        StandardBarJsonTransformation.version,
        page_size,
    )


def page(records, index, cursor):
    return ProviderPage(
        (RawRecord(0, records[index].payload, records[index].content_type),), cursor
    )


def test_pages_archive_once_resume_cursors_and_finalize_deterministic_dataset(setup):
    now, _, access, _, repository, objects, raw, datasets, market, _, bars, records = setup
    provider = ScriptedProvider([page(records, 0, "page-2"), page(records, 1, None)])
    service = worker(setup, provider)
    job = service.submit(access, request(setup))
    first = service.run_once(access, job.request.task_id, uuid4(), now)
    assert first.status == "queued" and len(first.batch_ids) == 1
    assert first.cursor == "page-2" and not first.collection_complete
    one = raw.read(access, first.batch_ids[0])
    assert one.records == (records[0],)
    second = service.run_once(access, job.request.task_id, uuid4(), now + timedelta(seconds=1))
    assert second.status == "queued" and second.collection_complete
    assert second.batch_ids == (job.request.batch_id(0), job.request.batch_id(1))
    completed = service.run_once(access, job.request.task_id, uuid4(), now + timedelta(seconds=2))
    assert completed.status == "completed" and completed.dataset_id == job.request.dataset_id
    assert provider.cursors == [None, "page-2"]
    assert len(datasets._datasets) == 1 and len(market._datasets) == 1
    metadata = datasets.get(job.request.workspace_id, job.request.dataset_id).metadata
    assert (
        metadata.batch_ids == completed.batch_ids and metadata.created_at == job.request.created_at
    )
    imported = market._datasets[(job.request.workspace_id, job.request.dataset_id)]
    assert [bar.timestamp for bar in imported] == [item.timestamp for item in bars]
    assert len(objects._objects) == 3  # Two immutable Parquet pages and one quality report.
    assert (
        service.run_once(access, job.request.task_id, uuid4(), now + timedelta(days=1)) == completed
    )
    assert len(provider.cursors) == 2
    assert repository.get(job.request.workspace_id, job.request.task_id) == completed


def test_tick_collection_archives_raw_pages_without_bar_dataset(setup):
    now, _, access, _, _, _, raw, datasets, market, _, _, records = setup
    provider = ScriptedProvider([page(records, 0, None)])
    service = worker(setup, provider)
    tick_request = replace(request(setup), granularity="tick")
    job = service.submit(access, tick_request)

    archived = service.run_once(access, job.request.task_id, uuid4(), now)
    assert archived.collection_complete and archived.status == "queued"
    assert raw.read(access, archived.batch_ids[0]).records == (records[0],)

    completed = service.run_once(access, job.request.task_id, uuid4(), now + timedelta(seconds=1))
    assert completed.status == "completed" and completed.dataset_id is None
    assert not datasets._datasets and not market._datasets


def test_overlapping_pages_preserve_both_raw_copies_but_write_one_normalized_bar(setup):
    now, _, access, _, _, objects, raw, datasets, market, _, _, records = setup
    provider = ScriptedProvider([page(records, 0, "overlap"), page(records, 0, None)])
    service = worker(setup, provider)
    job = service.submit(access, request(setup))
    for index in range(3):
        task = service.run_once(
            access, job.request.task_id, uuid4(), now + timedelta(seconds=index)
        )
    assert task.status == "completed" and len(task.batch_ids) == 2
    assert all(raw.read(access, batch_id).records == (records[0],) for batch_id in task.batch_ids)
    normalized = market._datasets[(job.request.workspace_id, job.request.dataset_id)]
    assert len(normalized) == 1
    publication = datasets.get(job.request.workspace_id, job.request.dataset_id).metadata
    assert publication.batch_ids == task.batch_ids
    report = json.loads(
        objects.get(
            job.request.workspace_id,
            publication.quality_report_key,
            expected_sha256=datasets.get(
                job.request.workspace_id, job.request.dataset_id
            ).quality_report_sha256,
        )
    )
    assert report["raw_record_count"] == 2 and report["bar_count"] == 1


def test_overlapping_but_conflicting_bars_fail_without_publishing_duplicate_data(setup):
    now, _, access, _, repository, _, raw, datasets, market, _, _, records = setup
    original = Bar.model_validate_json(records[0].payload.decode())
    conflicting = RawRecord(
        0,
        original.model_copy(update={"close": original.close + 1}).model_dump_json().encode(),
        "application/json",
    )
    provider = ScriptedProvider([page(records, 0, "overlap"), ProviderPage((conflicting,), None)])
    service = worker(setup, provider)
    job = service.submit(access, request(setup))
    service.run_once(access, job.request.task_id, uuid4(), now)
    service.run_once(access, job.request.task_id, uuid4(), now + timedelta(seconds=1))
    failed = service.run_once(access, job.request.task_id, uuid4(), now + timedelta(seconds=2))
    assert failed.status == "failed" and failed.last_error_code == ErrorCode.INVALID_INPUT
    assert len(failed.batch_ids) == 2 and all(
        raw.read(access, item).records for item in failed.batch_ids
    )
    assert not datasets._datasets and not market._datasets
    assert repository.get(job.request.workspace_id, job.request.task_id) == failed


def test_crash_after_page_staging_reuses_original_bytes_and_never_refetches(setup):
    now, _, access, _, repository, objects, raw, _, _, replay, _, records = setup
    provider = ScriptedProvider([ProviderPage((records[0],), None)])

    class FailOnce:
        def __init__(self):
            self.failed = False

        def publish(self, actor, draft):
            if not self.failed:
                self.failed = True
                raise PortError(ErrorCode.UNAVAILABLE, "object store temporarily unavailable")
            return raw.publish(actor, draft)

    service = worker(setup, provider, batch_service=FailOnce())
    job = service.submit(access, request(setup))
    failed = service.run_once(access, job.request.task_id, uuid4(), now)
    assert failed.status == "waiting" and failed.pending is not None and failed.failures == 1
    staged = failed.pending
    retry = service.run_once(access, job.request.task_id, uuid4(), failed.next_attempt_at)
    assert retry.status == "queued" and retry.pending is None
    assert retry.batch_ids == (job.request.batch_id(0),)
    assert provider.cursors == [None]
    assert raw.read(access, retry.batch_ids[0]).records == staged.page.records
    assert objects.get(
        access.workspace.workspace_id,
        raw.read(access, retry.batch_ids[0]).metadata.object_key,
        expected_sha256=raw.read(access, retry.batch_ids[0]).metadata.content_sha256,
    )
    assert repository.get(access.workspace.workspace_id, job.request.task_id) == retry
    assert replay is not None


def test_network_failures_use_exponential_backoff_and_rerun_preserves_checkpoint(setup):
    now, _, access, _, repository, _, raw, _, _, _, _, records = setup
    provider = ScriptedProvider(
        [
            page(records, 0, "next"),
            PortError(ErrorCode.TIMEOUT, "private vendor response"),
            PortError(ErrorCode.UNAVAILABLE, "retry"),
            page(records, 1, None),
        ]
    )
    service = worker(
        setup,
        provider,
        CollectionPolicy(
            min_interval_seconds=0.1, retry_base_seconds=1, retry_cap_seconds=8, max_failures=2
        ),
    )
    job = service.submit(access, request(setup))
    first = service.run_once(access, job.request.task_id, uuid4(), now)
    assert first.cursor == "next" and len(first.batch_ids) == 1
    waiting = service.run_once(access, job.request.task_id, uuid4(), now + timedelta(seconds=1))
    assert waiting.status == "waiting" and waiting.failures == 1
    assert waiting.last_error_code == ErrorCode.TIMEOUT
    assert "private vendor response" not in str(waiting)
    untouched = service.run_once(
        access, job.request.task_id, uuid4(), waiting.next_attempt_at - timedelta(milliseconds=1)
    )
    assert untouched == waiting and len(provider.cursors) == 2
    failed = service.run_once(access, job.request.task_id, uuid4(), waiting.next_attempt_at)
    assert failed.status == "failed" and failed.failures == 2
    assert failed.cursor == "next" and failed.batch_ids == first.batch_ids
    requeued = service.rerun(
        access, job.request.task_id, failed.next_attempt_at + timedelta(seconds=1)
    )
    assert requeued.status == "queued" and requeued.cursor == "next"
    assert requeued.batch_ids == first.batch_ids and requeued.failures == 0
    completed_page = service.run_once(
        access, job.request.task_id, uuid4(), requeued.next_attempt_at
    )
    assert completed_page.collection_complete
    assert provider.cursors == [None, "next", "next", "next"]
    assert raw.read(access, first.batch_ids[0]).records == (records[0],)


def test_provider_retry_after_is_persisted_and_releases_worker_lease(setup):
    now, _, access, _, repository, _, _, _, _, _, _, records = setup
    provider = ScriptedProvider(
        [
            PortError(ErrorCode.RATE_LIMITED, "rate", retry_after=30),
            ProviderPage((records[0],), None),
        ]
    )
    service = worker(setup, provider)
    job = service.submit(access, request(setup))
    waiting = service.run_once(access, job.request.task_id, uuid4(), now)
    assert waiting.status == "waiting" and waiting.failures == 1
    assert waiting.next_attempt_at == now + timedelta(seconds=30)
    throttled = repository.reserve_rate_slot(
        job.request, now + timedelta(seconds=10), timedelta(seconds=1)
    )
    assert throttled == waiting.next_attempt_at
    still_waiting = service.run_once(
        access, job.request.task_id, uuid4(), now + timedelta(seconds=29)
    )
    assert still_waiting == waiting and len(provider.cursors) == 1
    success = service.run_once(access, job.request.task_id, uuid4(), waiting.next_attempt_at)
    assert success.collection_complete and success.failures == 0
    assert provider.cursors == [None, None]


def test_repeated_cursor_is_rejected_and_failed_job_can_resume_after_provider_fix(setup):
    now, _, access, _, _, _, _, _, _, _, _, records = setup
    provider = ScriptedProvider(
        [
            page(records, 0, "same"),
            page(records, 1, "same"),
            page(records, 1, None),
        ]
    )
    service = worker(setup, provider)
    job = service.submit(access, request(setup))
    first = service.run_once(access, job.request.task_id, uuid4(), now)
    bad = service.run_once(access, job.request.task_id, uuid4(), now + timedelta(seconds=1))
    assert bad.status == "failed" and bad.last_error_code == ErrorCode.INVALID_INPUT
    assert bad.batch_ids == first.batch_ids + (job.request.batch_id(1),) and bad.pending is None
    resumed = service.rerun(access, job.request.task_id, now + timedelta(seconds=2))
    fixed = service.run_once(access, job.request.task_id, uuid4(), resumed.next_attempt_at)
    assert fixed.collection_complete and fixed.batch_ids == tuple(
        job.request.batch_id(index) for index in range(3)
    )
    assert provider.cursors == [None, "same", "same"]


def test_cursor_cycle_survives_worker_restart_and_archives_the_last_page(setup):
    now, _, access, _, repository, _, raw, datasets, market, replay, _, records = setup
    provider = ScriptedProvider(
        [page(records, 0, "A"), page(records, 1, "B"), page(records, 1, "A")]
    )
    job = repository.create(access.user_id, request(setup))
    service = worker(setup, provider)
    first = service.run_once(access, job.request.task_id, uuid4(), now)
    second = service.run_once(access, job.request.task_id, uuid4(), now + timedelta(seconds=1))
    assert len(first.seen_cursor_hashes) == 1 and len(second.seen_cursor_hashes) == 2
    failed = worker(setup, provider).run_once(
        access, job.request.task_id, uuid4(), now + timedelta(seconds=2)
    )
    assert failed.status == "failed" and failed.last_error_code == ErrorCode.INVALID_INPUT
    assert failed.pending is None and failed.cursor == "A" and len(failed.batch_ids) == 3
    assert all(raw.read(access, batch_id).records for batch_id in failed.batch_ids)
    assert not datasets._datasets and not market._datasets and replay is not None


def test_live_role_change_fences_existing_lease_and_expiry_allows_new_worker(setup):
    now, _, access, grants, repository, _, _, _, _, _, _, records = setup
    provider = ScriptedProvider([ProviderPage((records[0],), None)])
    job_request = request(setup)
    lease = repository.create(access.user_id, job_request)
    first = repository.claim(
        access.user_id,
        uuid4(),
        job_request.workspace_id,
        job_request.task_id,
        now,
        now + timedelta(seconds=5),
    )
    assert first is not None
    grants[access.user_id] = WorkspaceAccess(access.workspace, access.user_id, Role.VIEWER)
    with pytest.raises(PortError) as denied:
        repository.stage(first, StagedPage(ProviderPage((records[0],), None), now), now)
    assert denied.value.code == ErrorCode.REJECTED
    grants[access.user_id] = access
    replacement = repository.claim(
        access.user_id,
        uuid4(),
        job_request.workspace_id,
        job_request.task_id,
        now + timedelta(seconds=5),
        now + timedelta(seconds=10),
    )
    assert replacement is not None and replacement.token != first.token
    with pytest.raises(PortError) as stale:
        repository.stage(
            first, StagedPage(ProviderPage((records[0],), None), now), now + timedelta(seconds=5)
        )
    assert stale.value.code == ErrorCode.CONFLICT
    assert repository.get(job_request.workspace_id, job_request.task_id).pending is None
    assert lease.status == "queued" and provider.cursors == []


def test_concurrent_worker_invocations_fetch_only_one_page(setup):
    now, _, access, _, _, _, _, _, _, _, _, records = setup
    entered, release = Event(), Event()

    class BlockingProvider(ScriptedProvider):
        def fetch(self, request, cursor):
            entered.set()
            assert release.wait(timeout=3)
            return super().fetch(request, cursor)

    provider = BlockingProvider([ProviderPage((records[0],), None)])
    service = worker(setup, provider)
    job = service.submit(access, request(setup))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.run_once, access, job.request.task_id, uuid4(), now)
        assert entered.wait(timeout=3)
        other = service.run_once(access, job.request.task_id, uuid4(), now)
        release.set()
        result = first.result(timeout=3)
    assert other.status == "running" and result.status == "queued"
    assert provider.cursors == [None]


def test_source_throttle_is_shared_between_tasks_and_rate_wait_is_not_a_failure(setup):
    now, _, access, _, _, _, _, _, _, _, _, records = setup
    provider = ScriptedProvider([page(records, 0, "next"), page(records, 1, None)])
    service = worker(setup, provider, CollectionPolicy(min_interval_seconds=10))
    one = service.submit(access, request(setup))
    two = service.submit(access, request(setup))
    first = service.run_once(access, one.request.task_id, uuid4(), now)
    second = service.run_once(access, two.request.task_id, uuid4(), now)
    assert first.status == "queued"
    assert second.status == "waiting" and second.failures == 0
    assert second.next_attempt_at == now + timedelta(seconds=10)
    assert provider.cursors == [None]
    fetched = service.run_once(access, two.request.task_id, uuid4(), second.next_attempt_at)
    assert fetched.batch_ids == (two.request.batch_id(0),) and len(provider.cursors) == 2


def test_request_and_provider_pages_are_bounded_and_validation_hides_payloads(setup):
    now, workspace = setup[:2]
    with pytest.raises(PortError):
        CollectionRequest(
            workspace.workspace_id, uuid4(), "source", "license", "A", "1m", now, now, now, "v1"
        )
    with pytest.raises(PortError):
        CollectionRequest(
            workspace.workspace_id,
            uuid4(),
            "source",
            "license",
            "A",
            "1m",
            now,
            now + timedelta(seconds=1),
            now,
            "v1",
            page_size=0,
        )
    with pytest.raises(PortError):
        ProviderPage((RawRecord(0, b"x" * 16_000_001),), None)
    with pytest.raises(PortError) as invalid:
        ProviderPage((RawRecord(0, b"private"),), "" * 3000)
    assert "private" not in str(invalid.value)


def test_submit_rejects_unconfigured_source_and_nonmanagers_before_persisting(setup):
    _, _, access, _, repository, _, raw, datasets, market, replay, _, _ = setup
    provider = ScriptedProvider([])
    service = worker(setup, provider)
    with pytest.raises(PortError) as missing:
        service.submit(
            access,
            replace(request(setup), source="unknown"),
        )
    assert missing.value.code == ErrorCode.UNSUPPORTED
    with pytest.raises(PortError) as forbidden:
        service.submit(replace(access, role=Role.VIEWER), request(setup))
    assert forbidden.value.code == ErrorCode.REJECTED
    assert not repository._tasks and not raw._repository._batches
    assert not datasets._datasets and not market._datasets and replay is not None


def test_local_rate_wait_does_not_extend_global_source_cooldown(setup):
    now, _, access, _, repository, _, _, _, _, _, _, records = setup
    provider = ScriptedProvider([ProviderPage((records[0],), None)])
    service = worker(setup, provider, policy=CollectionPolicy(min_interval_seconds=1))
    first = service.submit(access, request(setup))
    service.run_once(access, first.request.task_id, uuid4(), now)
    for offset in (0.1, 0.4, 0.8):
        contender = service.submit(access, request(setup))
        waiting = service.run_once(
            access, contender.request.task_id, uuid4(), now + timedelta(seconds=offset)
        )
        assert waiting.status == "waiting" and waiting.failures == 0
    # Contenders waited locally; a new task can use the original one-second slot.
    assert repository.reserve_rate_slot(
        first.request, now + timedelta(seconds=1), timedelta(seconds=1)
    ) == now + timedelta(seconds=1)
    assert len(provider.cursors) == 1
