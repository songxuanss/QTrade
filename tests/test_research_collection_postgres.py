"""Controlled adapter execution with real isolated persistence, never live evidence."""

from datetime import date
from uuid import uuid4

import pytest
from test_research_contracts import statement
from test_research_evidence import NOW
from test_research_evidence_postgres import database as isolated_database
from test_research_interface import ControlledSource, request

from qtrade_data_contracts.research_transport import ResearchQuery
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchRawPage, ResearchService
from qtrade_data_core.research_collection import ResearchCollectionExecutor
from qtrade_data_core.research_release import ResearchReleaseService
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_postgres.research_publications import (
    PostgresResearchPublications,
    PostgresResearchReader,
)

database = isolated_database


class PagedFixture(ControlledSource):
    def normalize(self, req, page, collected_at):
        return (
            statement(
                scope_id=req.scope_id,
                source=req.source,
                mapping_version=self.capability.mapping_version,
                collected_at=collected_at,
                disclosure_date=date(2015, 8, 28) if page.cursor is None else date(2016, 8, 27),
            ),
        )


def setup(database, tmp_path):
    req = request(scope_id=uuid4(), request_id=uuid4())
    auth = StaticScopeAuthorizer({f"{req.actor_id}:{req.scope_id}": "manage"})
    provider = PagedFixture()
    archive = FileResearchArchive(tmp_path)
    repo = PostgresResearchPublications(database)
    service = ResearchService(auth, (provider,), PostgresResearchReader(repo, archive))
    plan = service.preview(req)
    executor = ResearchCollectionExecutor(auth, archive, repo, clock=lambda: NOW)
    return req, provider, archive, repo, service, plan, executor, auth


def test_adapter_paging_to_release_and_refreshed_query(database, tmp_path):
    req, provider, archive, repo, service, plan, executor, auth = setup(database, tmp_path)
    assert provider.fetch_calls == 0
    result = executor.collect(plan, provider)
    assert len(result.raw_batches) == 2 and result.artifact.record_count == 2
    assert all(archive.raw(raw) for raw in result.raw_batches)
    release = ResearchReleaseService(auth, archive, repo)
    published = release.publish(req.actor_id, req.scope_id, result.artifact.artifact_id)
    assert published.quality.status == "warning"
    assert release.publish(req.actor_id, req.scope_id, result.artifact.artifact_id) == published
    restarted = ResearchService(
        auth,
        (provider,),
        PostgresResearchReader(
            PostgresResearchPublications(database), FileResearchArchive(tmp_path)
        ),
    )
    query = ResearchQuery(
        actor_id=req.actor_id,
        scope_id=req.scope_id,
        publication_id=published.publication_id,
        content_version=published.content_version,
        kind=req.kind,
        limit=1,
    )
    first = restarted.records(query)
    second = restarted.records(query.model_copy(update={"cursor": first.next_cursor}))
    assert second.next_cursor is None
    assert first.records[0].disclosure_date != second.records[0].disclosure_date
    assert executor.collect(plan, provider).artifact == result.artifact


@pytest.mark.parametrize("rejection", [DataFailure("unverified"), ValueError("private input")])
def test_normalization_failure_retains_raw_before_conversion(database, tmp_path, rejection):
    req, provider, archive, repo, _, plan, executor, _ = setup(database, tmp_path)
    original_normalize = provider.normalize

    def fail(*args):
        assert repo.stage(req.scope_id, req.request_id) == "raw_persisted"
        raise rejection

    provider.normalize = fail
    with pytest.raises(DataFailure, match="unverified"):
        executor.collect(plan, provider)
    assert repo.stage(req.scope_id, req.request_id) == "raw_persisted"
    assert list(archive.root.glob("*/*/raw-manifest.json"))
    failures = archive.processing_failures(plan)
    assert len(failures) == 1 and failures[0].normalized_record_count is None
    assert failures[0].checker_version == "research-normalization-check.v1"
    assert archive.raw(repo.raw_manifest(req.scope_id, failures[0].batch_id))
    original = failures[0]
    with pytest.raises(DataFailure, match="unverified"):
        executor.collect(plan, provider)
    assert FileResearchArchive(tmp_path).processing_failures(plan) == (original,)
    # Repair the conversion without changing frozen input or rewriting its
    # rejection history. This is controlled recovery, not live supplier proof.
    provider.normalize = original_normalize
    recovered = executor.collect(plan, provider)
    assert recovered.artifact.record_count == 2
    raw_ids = {raw.batch_id for raw in recovered.raw_batches}
    assert original.batch_id in raw_ids
    assert FileResearchArchive(tmp_path).processing_failures(plan) == (original,)


def test_unexpected_conversion_failure_can_retry_without_losing_raw(database, tmp_path):
    from qtrade_data_core.research_worker import ResearchWorker
    from qtrade_data_postgres.research_jobs import PostgresResearchJobs

    req, provider, archive, repo, _, plan, executor, auth = setup(database, tmp_path)
    original_normalize = provider.normalize

    def broken(*args):
        raise RuntimeError("private supplier detail must never reach the job")

    provider.normalize = broken
    queue = PostgresResearchJobs(database)
    queue.enqueue(plan)
    worker = ResearchWorker(
        queue, executor, ResearchReleaseService(auth, archive, repo), (provider,)
    )
    assert worker.run_once()
    failed = PostgresResearchJobs(database).job(req.scope_id, req.request_id)
    assert failed["state"] == "failed" and failed["error_code"] == "unavailable"
    assert failed["publication_id"] is None
    assert repo.stage(req.scope_id, req.request_id) == "raw_persisted"
    assert not archive.processing_failures(plan)  # Not a certified input rejection.
    before = {p: p.read_bytes() for p in archive.root.glob("*/*/raw-manifest.json")}
    assert before
    provider.normalize = original_normalize
    queue.retry(req.scope_id, req.request_id)
    assert worker.run_once()
    completed = queue.job(req.scope_id, req.request_id)
    assert completed["state"] == "completed"
    publication = repo.manifest(req.scope_id, completed["publication_id"])
    assert publication.artifact.record_count == 2
    assert all(p.read_bytes() == content for p, content in before.items())
    assert all(archive.raw(raw) for raw in publication.artifact.raw_batches)


def test_worker_rejection_check_scoped_reader_and_archive_failure(database, tmp_path, monkeypatch):
    from qtrade_data_core.research_jobs import ResearchJobsService
    from qtrade_data_core.research_worker import ResearchWorker
    from qtrade_data_postgres.research_jobs import PostgresResearchJobs

    req, provider, archive, repo, research, plan, executor, auth = setup(database, tmp_path)

    def rejected(*args):
        raise DataFailure("unverified")

    provider.normalize = rejected
    queue = PostgresResearchJobs(database)
    queue.enqueue(plan)
    worker = ResearchWorker(
        queue, executor, ResearchReleaseService(auth, archive, repo), (provider,)
    )
    assert worker.run_once()
    stored = queue.job(req.scope_id, req.request_id)
    assert stored["state"] == "failed" and stored["publication_id"] is None
    jobs = ResearchJobsService(auth, research, queue)
    reports = jobs.processing_failures(req.actor_id, req.scope_id, req.request_id)
    assert len(reports) == 1 and reports[0].normalized_record_count is None
    assert archive.raw(repo.raw_manifest(req.scope_id, reports[0].batch_id))
    assert FileResearchArchive(tmp_path).processing_failures(plan) == reports
    with pytest.raises(DataFailure, match="forbidden"):
        jobs.processing_failures(req.actor_id, uuid4(), req.request_id)
    with pytest.raises(DataFailure, match="not_found"):
        jobs.processing_failures(req.actor_id, req.scope_id, uuid4())

    def unavailable(*args):
        raise OSError("fixture archive failure")

    monkeypatch.setattr(archive, "record_processing_failure", unavailable)
    queue.retry(req.scope_id, req.request_id)
    assert worker.run_once()
    assert queue.job(req.scope_id, req.request_id)["error_code"] == "unavailable"
    assert archive.processing_failures(plan) == reports


def test_empty_and_cyclic_provider_do_not_publish(database, tmp_path):
    req, provider, archive, repo, _, plan, executor, _ = setup(database, tmp_path)
    provider.fetch = lambda *args: ResearchRawPage((), None, None)
    provider.normalize = lambda *args: ()
    result = executor.collect(plan, provider)
    assert result.status == "no_observations" and result.artifact is None
    assert result.raw_batches[0].raw_record_count == 0
    req, provider, archive, repo, _, plan, executor, _ = setup(database, tmp_path / "cycle")
    provider.fetch = lambda request, partition, cursor: ResearchRawPage(
        ({"fixture": True},), cursor, "loop"
    )
    with pytest.raises(DataFailure, match="unverified"):
        executor.collect(plan, provider)
    assert repo.stage(req.scope_id, req.request_id) == "raw_persisted"


class TypedFixture(PagedFixture):
    def __init__(self, kind, axis, mapping="fixture.v1"):
        super().__init__()
        self.capability = self.capability.model_copy(
            update={"kind": kind, "date_axes": (axis,), "mapping_version": mapping}
        )

    def normalize(self, req, page, collected_at):
        from qtrade_data_contracts.research import (
            AdjustmentFactor,
            CorporateAction,
            MembershipInterval,
            SecurityIdentityChange,
        )

        second = page.cursor is not None
        common = dict(
            scope_id=req.scope_id,
            source=req.source,
            instrument_id="600519.SH",
            collected_at=collected_at,
            mapping_version=self.capability.mapping_version,
        )
        when = date(2015, 7, 1) if second else date(2015, 6, 1)
        if req.kind == "membership_interval":
            record = MembershipInterval(
                **common,
                universe_id="fixture-pool",
                valid_from=when,
                valid_until=date(2015, 8, 1) if second else date(2015, 7, 1),
                interval_evidence="fixture:membership",
            )
        elif req.kind == "security_identity_change":
            record = SecurityIdentityChange(
                **common,
                security_id="fixture-security",
                change="name",
                effective_date=when,
                name="受控新名" if second else "受控旧名",
                change_evidence="fixture:identity",
            )
        elif req.kind == "corporate_action":
            record = CorporateAction(
                **common,
                source_event_id="fixture-event",
                action="distribution",
                status="implemented",
                basis_shares="10",
                cash="2" if second else "1",
                cash_currency="CNY",
                announcement_date=date(2015, 6, 1),
                terms_evidence="fixture:terms",
            )
        else:
            record = AdjustmentFactor(
                **common,
                observation_date=when,
                adjustment="forward",
                factor="0.9",
                base_date=date(2026, 10, 9),
                base_value="1",
                algorithm_version="fixture.v1",
                convention_evidence="fixture:adjustment",
            )
        return (record,)


@pytest.mark.parametrize(
    "kind,axis",
    [
        ("membership_interval", "effective_date"),
        ("security_identity_change", "effective_date"),
        ("corporate_action", "event_date"),
        ("adjustment_factor", "observation_date"),
    ],
)
def test_typed_adapter_full_lifecycle_and_mapping_migration(database, tmp_path, kind, axis):
    req, _, archive, repo, _, _, executor, auth = setup(database, tmp_path)
    provider = TypedFixture(kind, axis)
    req = req.model_copy(update={"kind": kind, "date_axis": axis})
    service = ResearchService(auth, (provider,), PostgresResearchReader(repo, archive))
    assert service.catalog(req.actor_id, req.scope_id)[0].evidence == "fixture"
    plan = service.preview(req)
    result = executor.collect(plan, provider)
    assert len(result.raw_batches) == 2 and result.artifact.record_count == 2
    release = ResearchReleaseService(auth, archive, repo)
    old = release.publish(req.actor_id, req.scope_id, result.artifact.artifact_id)
    q = ResearchQuery(
        actor_id=req.actor_id,
        scope_id=req.scope_id,
        publication_id=old.publication_id,
        content_version=old.content_version,
        kind=kind,
    )
    old_records = service.records(q).records
    assert len(old_records) == 2 and all(r.kind == kind for r in old_records)
    if kind == "corporate_action":
        assert old_records[0].event_identity == old_records[1].event_identity
        assert old_records[0].content_version != old_records[1].content_version
    # Mapping upgrades get a new frozen plan/run; the previous reference is unchanged.
    upgraded = TypedFixture(kind, axis, "fixture.v2")
    new_req = req.model_copy(update={"request_id": uuid4()})
    new_service = ResearchService(auth, (upgraded,), PostgresResearchReader(repo, archive))
    new_result = executor.collect(new_service.preview(new_req), upgraded)
    new = release.publish(req.actor_id, req.scope_id, new_result.artifact.artifact_id)
    assert new.publication_id != old.publication_id
    assert service.records(q).records == old_records
    assert executor.collect(plan, provider).artifact == result.artifact
    assert release.publish(req.actor_id, req.scope_id, result.artifact.artifact_id) == old


def test_member_overlap_blocks_release_with_raw_preserved(database, tmp_path):
    req, _, archive, repo, _, _, executor, auth = setup(database, tmp_path)
    provider = TypedFixture("membership_interval", "effective_date")
    req = req.model_copy(update={"kind": "membership_interval", "date_axis": "effective_date"})
    original = provider.normalize

    def overlapping(req, page, collected_at):
        record = original(req, page, collected_at)[0]
        return (record.model_copy(update={"valid_until": date(2015, 9, 1)}),)

    provider.normalize = overlapping
    result = executor.collect(ResearchService(auth, (provider,)).preview(req), provider)
    release = ResearchReleaseService(auth, archive, repo)
    quality = release.review(req.actor_id, req.scope_id, result.artifact.artifact_id)
    assert quality.status == "blocked" and "member_overlap" in {i.code for i in quality.issues}
    with pytest.raises(DataFailure, match="unverified"):
        release.publish(req.actor_id, req.scope_id, result.artifact.artifact_id)
    assert all(archive.raw(raw) for raw in result.raw_batches)


def test_duplicate_event_observation_blocked_without_rewriting_versions(database, tmp_path):
    req, _, archive, repo, _, _, executor, auth = setup(database, tmp_path)
    provider = TypedFixture("corporate_action", "event_date")
    req = req.model_copy(update={"kind": "corporate_action", "date_axis": "event_date"})
    original = provider.normalize
    provider.normalize = lambda req, page, when: original(
        req, ResearchRawPage(page.rows, None, None), when
    )
    result = executor.collect(ResearchService(auth, (provider,)).preview(req), provider)
    release = ResearchReleaseService(auth, archive, repo)
    quality = release.review(req.actor_id, req.scope_id, result.artifact.artifact_id)
    assert quality.status == "blocked"
    assert "duplicate_record" in {i.code for i in quality.issues}
