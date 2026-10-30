"""Real isolated persistence for controlled offline replay, not supplier evidence."""

from uuid import uuid4

import psycopg
import pytest
from test_research_collection_postgres import setup
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_contracts.research_replay import ResearchReplayLimits, ResearchReplayRequest
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchRawPage
from qtrade_data_core.research_release import ResearchReleaseService
from qtrade_data_core.research_replay import ResearchReplayService
from qtrade_data_core.research_worker import ResearchWorker
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_postgres.research_replay import PostgresResearchReplays

database = isolated_database


def no_network(*args, **kwargs):
    raise AssertionError("offline replay attempted supplier discovery/fetch")


def replay_service(database, tmp_path, provider, auth, **kwargs):
    repo = PostgresResearchReplays(database)
    return ResearchReplayService(auth, FileResearchArchive(tmp_path), repo, (provider,), **kwargs)


def command(req):
    return ResearchReplayRequest(
        actor_id=req.actor_id,
        scope_id=req.scope_id,
        request_id=uuid4(),
        parent_request_id=req.request_id,
    )


def worker(service, executor):
    return ResearchWorker(
        service.repository,
        executor,
        ResearchReleaseService(service.auth, service.archive, service.repository),
        service.providers,
        replays=service,
    )


def test_offline_new_mapping_release_preserves_old_reference_and_inputs(database, tmp_path):
    req, provider, archive, repo, _, plan, executor, auth = setup(database, tmp_path)
    original = executor.collect(plan, provider)
    publication = ResearchReleaseService(auth, archive, repo).publish(
        req.actor_id, req.scope_id, original.artifact.artifact_id
    )
    frozen = {p: p.read_bytes() for p in archive.root.rglob("*") if p.is_file()}
    calls = provider.fetch_calls
    provider.capability = provider.capability.model_copy(
        update={
            "mapping_version": "fixture.v2",
            "configured": False,
            "unblock_condition": "fixture: network disabled; local converter retained",
        }
    )
    provider.fetch = provider.partitions = provider.estimate = no_network
    service = replay_service(database, tmp_path, provider, auth)
    cmd = command(req)
    preview = service.preview(cmd)
    assert preview.target_plan.estimated_calls_max == 0
    assert preview.target_plan.capability.configured is False  # No new network permission claim.
    with pytest.raises(DataFailure, match="not_found"):
        service.repository.job(req.scope_id, cmd.request_id)
    assert service.submit(cmd, preview.control_sha256)["state"] == "queued"
    assert service.submit(cmd, preview.control_sha256)["state"] == "queued"
    assert service.repository.replay(req.scope_id, cmd.request_id) == preview
    assert worker(service, executor).run_once()
    job = service.repository.job(req.scope_id, cmd.request_id)
    assert job["state"] == "completed" and job["publication_id"] != publication.publication_id
    new = service.repository.manifest(req.scope_id, job["publication_id"])
    assert new.artifact.mapping_version == "fixture.v2" and new.artifact.record_count == 2
    assert {r.collected_at for r in archive.records(new.artifact)} == {
        r.collected_at for r in archive.records(publication.artifact)
    }
    assert service.repository.manifest(req.scope_id, publication.publication_id) == publication
    assert all(p.read_bytes() == content for p, content in frozen.items())
    assert provider.fetch_calls == calls
    for table in ("research_replay", "research_replay_input"):
        with psycopg.connect(database, autocommit=True) as conn, pytest.raises(psycopg.Error):
            conn.execute(f"UPDATE qtrade_data.{table} SET request_id=request_id")
    # Only trusted hard-coded table names above enter SQL, never user identifiers.


def test_blocked_conversion_can_replay_complete_raw_after_fix(database, tmp_path):
    req, provider, archive, repo, _, plan, executor, auth = setup(database, tmp_path)
    provider.fetch = lambda *args: ResearchRawPage(({"fixture": True},), None, None)
    original_normalize = provider.normalize
    provider.normalize = lambda *args: (_ for _ in ()).throw(DataFailure("unverified"))
    with pytest.raises(DataFailure, match="unverified"):
        executor.collect(plan, provider)
    failure = archive.processing_failures(plan)
    provider.normalize = original_normalize
    provider.capability = provider.capability.model_copy(update={"mapping_version": "fixture.v2"})
    provider.fetch = provider.partitions = provider.estimate = no_network
    service = replay_service(database, tmp_path, provider, auth)
    cmd = command(req)
    value = service.preview(cmd)
    service.submit(cmd, value.control_sha256)
    assert worker(service, executor).run_once()
    job = service.repository.job(req.scope_id, cmd.request_id)
    assert job["state"] == "completed"
    assert archive.processing_failures(plan) == failure
    assert repo.stage(req.scope_id, req.request_id) == "raw_persisted"
    assert all(archive.raw(b) for b in value.source_batches)


def test_incomplete_chain_and_hard_limits_refuse_without_network_or_new_job(database, tmp_path):
    req, provider, archive, _, _, plan, executor, auth = setup(database, tmp_path)
    normalize = provider.normalize
    provider.normalize = lambda *args: (_ for _ in ()).throw(DataFailure("unverified"))
    with pytest.raises(DataFailure, match="unverified"):
        executor.collect(plan, provider)  # First page advertises an absent second page.
    service = replay_service(database, tmp_path, provider, auth)
    with pytest.raises(DataFailure, match="unverified"):
        service.preview(command(req))
    provider.normalize = normalize
    executor.collect(plan, provider)
    provider.fetch = provider.partitions = provider.estimate = no_network
    for limits in (
        ResearchReplayLimits(max_pages=1),
        ResearchReplayLimits(max_rows=1),
        ResearchReplayLimits(max_bytes=1),
    ):
        bounded = replay_service(database, tmp_path, provider, auth, limits=limits)
        with pytest.raises(DataFailure, match="invalid_input"):
            bounded.preview(command(req))


def test_corrupt_source_after_acceptance_blocks_worker_without_publication(database, tmp_path):
    req, provider, archive, _, _, plan, executor, auth = setup(database, tmp_path)
    executor.collect(plan, provider)
    service = replay_service(database, tmp_path, provider, auth)
    cmd = command(req)
    value = service.preview(cmd)
    service.submit(cmd, value.control_sha256)
    original = value.source_batches[0]
    path = (
        archive.root
        / str(req.scope_id)
        / str(original.batch_id)
        / f"raw-{original.raw_sha256}.parquet"
    )
    path.write_bytes(b"corrupted")
    provider.fetch = provider.partitions = provider.estimate = no_network
    assert worker(service, executor).run_once()
    job = service.repository.job(req.scope_id, cmd.request_id)
    assert job["state"] == "failed" and job["error_code"] == "unverified"
    assert job["publication_id"] is None


def test_current_auth_stale_control_and_atomic_rollback(database, tmp_path):
    req, provider, archive, _, _, plan, executor, auth = setup(database, tmp_path)
    original = executor.collect(plan, provider)
    service = replay_service(database, tmp_path, provider, auth)
    cmd = command(req)
    value = service.preview(cmd)
    with pytest.raises(DataFailure, match="conflict"):
        service.submit(cmd, "0" * 64)
    # An invalid input reference fails after tentative run insertion; all intent,
    # job and new run changes must roll back together.
    bad = value.model_copy(
        update={
            "source_batches": (
                value.source_batches[0].model_copy(update={"raw_sha256": "0" * 64}),
                *value.source_batches[1:],
            )
        }
    )
    from qtrade_data_contracts.collection import digest
    from qtrade_data_contracts.research_replay import ResearchReplay

    bad = ResearchReplay.model_validate_json(
        bad.model_copy(
            update={
                "control_sha256": digest(bad.model_dump(mode="json", exclude={"control_sha256"}))
            }
        ).model_dump_json()
    )
    with pytest.raises(DataFailure, match="conflict"):
        service.repository.enqueue_replay(bad)
    with pytest.raises(DataFailure, match="not_found"):
        service.repository.plan(req.scope_id, cmd.request_id)
    assert service.repository.replay(req.scope_id, cmd.request_id) is None
    readonly = StaticScopeAuthorizer({f"{req.actor_id}:{req.scope_id}": "read"})
    denied = replay_service(database, tmp_path, provider, readonly)
    with pytest.raises(DataFailure, match="forbidden"):
        denied.preview(cmd)
    service.submit(cmd, value.control_sha256)
    with pytest.raises(DataFailure, match="forbidden"):
        denied.execute(value.target_plan, value)
    with pytest.raises(DataFailure, match="forbidden"):
        service.read(req.actor_id, uuid4(), cmd.request_id)
    assert archive.records(original.artifact)
    assert worker(denied, executor).run_once()
    assert service.repository.job(req.scope_id, cmd.request_id)["error_code"] == "forbidden"


def test_execution_rechecks_mapping_limits_and_never_falls_back_to_network(database, tmp_path):
    req, provider, archive, _, _, plan, executor, auth = setup(database, tmp_path)
    executor.collect(plan, provider)
    service = replay_service(database, tmp_path, provider, auth)
    cmd = command(req)
    value = service.preview(cmd)
    service.submit(cmd, value.control_sha256)
    provider.fetch = provider.partitions = provider.estimate = no_network
    provider.capability = provider.capability.model_copy(update={"mapping_version": "fixture.v2"})
    with pytest.raises(DataFailure, match="unsupported"):
        service.execute(value.target_plan, value)
    provider.capability = value.target_plan.capability
    tightened = replay_service(
        database, tmp_path, provider, auth, limits=ResearchReplayLimits(max_rows=1)
    )
    with pytest.raises(DataFailure, match="invalid_input"):
        tightened.execute(value.target_plan, value)
    # An older composition without replay wiring must reject the explicit local
    # intent. Treating its metadata as absent must never invoke provider.fetch.
    normal = ResearchWorker(
        service.repository,
        executor,
        ResearchReleaseService(auth, archive, service.repository),
        (provider,),
    )
    assert normal.run_once()
    assert service.repository.job(req.scope_id, cmd.request_id)["error_code"] == "unsupported"


def test_replay_bounds_uncompressed_payload_before_decoding(database, tmp_path):
    req, provider, archive, _, _, plan, executor, auth = setup(database, tmp_path)
    provider.fetch = lambda *args: ResearchRawPage(({"large": "x" * 100000},), None, None)
    executor.collect(plan, provider)
    service = replay_service(
        database, tmp_path, provider, auth, limits=ResearchReplayLimits(max_bytes=10000)
    )
    # Highly compressible JSON fits the stored-file limit but exceeds the
    # uncompressed Parquet budget; a row-count-only cap would miss this case.
    with pytest.raises(DataFailure, match="invalid_input"):
        service.preview(command(req))
