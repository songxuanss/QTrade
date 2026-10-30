"""Controlled input/converter with real queue, atomic intents and ordinary gates."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError
from test_normalization_inputs import fail_network, row
from test_normalization_inputs import setup as prepared_fixture
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_contracts.collection import CollectionJobRequest, digest
from qtrade_data_contracts.collection_replay import (
    CollectionReplay,
    CollectionReplayLimits,
    CollectionReplayRequest,
)
from qtrade_data_core.collection import CollectionService
from qtrade_data_core.collection_replay import CollectionReplayService
from qtrade_data_core.freshness_evidence import DailyPublicationEvidence
from qtrade_data_core.ports import DataFailure
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_postgres.collection_replay import PostgresCollectionReplays

database = isolated_database


def setup(database, tmp_path, *, factors=False, blocked=False, daily=False):
    provider, task, archive, _, _, _, _, calls = prepared_fixture(
        tmp_path, factors=factors, rows=[row("2025-09-01 00:00:00")] if daily else None
    )
    provider.names = lambda ids: dict.fromkeys(ids, "农业银行")
    request = task.request.model_copy(update={"mode": "all"})
    if daily:
        request = request.model_copy(
            update={
                "granularity": "1d",
                "source": "mairui.history.bars",
                "start": datetime(2025, 8, 31, 16, tzinfo=UTC),
                "end": datetime(2025, 9, 1, 16, tzinfo=UTC),
            }
        )
    auth = StaticScopeAuthorizer({f"{request.actor_id}:{request.scope_id}": "manage"})
    repo = PostgresCollectionReplays(database)
    normal = CollectionService(auth, repo, archive, (provider,))
    plan = normal.preview(request)
    normal.submit(request, plan.coverage_version)
    mapper = provider.normalize
    if blocked:
        provider.normalize = lambda *args: (_ for _ in ()).throw(DataFailure("unverified"))
    parent = normal.run_once()
    provider.normalize = mapper
    inputs = archive.normalization_inputs(parent)
    assert len(inputs) == 1
    replay = CollectionReplayService(auth, archive, repo, (provider,))
    normal.replays = replay
    command = CollectionReplayRequest(
        actor_id=request.actor_id,
        scope_id=request.scope_id,
        request_id=uuid4(),
        parent_job_id=request.request_id,
        parent_part_id=parent.part_id,
        input_id=inputs[0].input_id,
    )
    return request, parent, provider, archive, repo, auth, normal, replay, command, calls


def disable_acquisition(provider):
    provider.fetch = provider.fetch_prepared = provider._get = provider.factors = provider.names = (
        provider.partitions
    ) = provider.estimate = fail_network


@pytest.mark.parametrize("factors", [False, True])
def test_offline_worker_release_old_reference_and_replay_of_replay(database, tmp_path, factors):
    req, parent, provider, archive, repo, auth, normal, replay, command, calls = setup(
        database, tmp_path, factors=factors
    )
    original = repo.publication(req.scope_id, parent.part_id)
    frozen = {p: p.read_bytes() for p in archive.root.rglob("*") if p.is_file()}
    count = len(calls)
    disable_acquisition(provider)
    preview = replay.preview(command)
    assert preview.supplier_calls == 0 and preview.sources
    assert len(repo.jobs(req.scope_id)) == 1
    accepted = replay.submit(command, preview.control_sha256)
    assert (
        accepted["state"] == "running"
        and accepted["parts"][0]["execution_kind"] == "normalization_replay"
    )
    assert replay.submit(command, preview.control_sha256) == accepted
    assert normal.run_once().job_id == command.request_id
    result = repo.job(req.scope_id, command.request_id)
    assert result["state"] == "completed"
    new = repo.publication(req.scope_id, preview.target_part_id)
    assert new.publication_id != original.publication_id
    assert archive.raw_records(new) == archive.raw_records(original)
    assert archive.records(new) == archive.records(original)
    assert new.evidence["normalization_replay"]["input_id"] == str(command.input_id)
    assert (
        archive.normalization_inputs(
            repo.part_for_job(req.scope_id, command.request_id, new.part_id)
        )
        == ()
    )
    assert (
        len(archive.qualities(repo.part_for_job(req.scope_id, command.request_id, new.part_id)))
        == 1
    )
    assert all(p.read_bytes() == body for p, body in frozen.items()) and len(calls) == count
    # A second-generation replay resolves the same root directly. No source
    # receipts are forged under the child, and no unbounded ancestry walk occurs.
    again = command.model_copy(
        update={
            "request_id": uuid4(),
            "parent_job_id": command.request_id,
            "parent_part_id": new.part_id,
        }
    )
    candidate = replay.preview(again)
    assert candidate.input == preview.input
    replay.submit(again, candidate.control_sha256)
    assert normal.run_once().job_id == again.request_id
    assert repo.job(req.scope_id, again.request_id)["state"] == "completed"
    assert len(calls) == count and all(p.read_bytes() == body for p, body in frozen.items())
    assert replay.inputs(req.actor_id, req.scope_id, command.request_id, new.part_id) == (
        preview.input,
    )


@pytest.mark.parametrize("factors", [False, True])
def test_blocked_original_can_publish_after_registered_mapper_fix(database, tmp_path, factors):
    req, parent, provider, archive, repo, _, normal, replay, command, calls = setup(
        database, tmp_path, blocked=True, factors=factors
    )
    assert repo.job(req.scope_id, req.request_id)["state"] == "failed"
    failures = archive.processing_failures(parent)
    assert failures and archive.recover(parent) is None
    datasets = provider.datasets
    provider.datasets = lambda: tuple(
        d.model_copy(update={"mapping_version": d.mapping_version + ".fixed"}) for d in datasets()
    )
    count = len(calls)
    disable_acquisition(provider)
    value = replay.preview(command)
    assert value.target_mapping_version != value.input.mapping_version
    replay.submit(command, value.control_sha256)
    normal.run_once()
    assert repo.job(req.scope_id, command.request_id)["state"] == "completed"
    assert archive.processing_failures(parent) == failures and archive.recover(parent) is None
    assert len(calls) == count


def test_stale_control_current_limits_corrupt_source_and_read_scope(database, tmp_path):
    req, parent, provider, archive, repo, auth, normal, replay, command, _ = setup(
        database, tmp_path
    )
    value = replay.preview(command)
    with pytest.raises(DataFailure, match="conflict"):
        replay.submit(command, "0" * 64)
    with pytest.raises(DataFailure, match="not_found"):
        replay.read(req.actor_id, req.scope_id, command.request_id)
    replay.submit(command, value.control_sha256)
    # A stricter bound that still admits these inputs must not invalidate a
    # frozen intent merely because configuration bytes changed.
    replay.limits = CollectionReplayLimits(max_bytes=value.verified_bytes + 1)
    disable_acquisition(provider)
    normal.run_once()
    assert repo.job(req.scope_id, command.request_id)["state"] == "completed"
    fresh = command.model_copy(update={"request_id": uuid4()})
    preview = replay.preview(fresh)
    replay.submit(fresh, preview.control_sha256)
    cap = value.sources[-1]
    path = (
        archive.root
        / str(req.scope_id)
        / str(parent.part_id)
        / "source-captures"
        / f"payload-{cap.content_sha256}.json"
    )
    path.write_bytes(b"corrupt")
    normal.run_once()
    result = repo.job(req.scope_id, fresh.request_id)
    assert result["state"] == "failed" and result["parts"][0]["error_code"] == "unverified"
    assert result["parts"][0]["publication"] is None
    auth.grants[f"{req.actor_id}:{req.scope_id}"] = "read"
    assert replay.read(req.actor_id, req.scope_id, command.request_id) == value
    with pytest.raises(DataFailure, match="forbidden"):
        replay.submit(command, value.control_sha256)
    with pytest.raises(DataFailure, match="forbidden"):
        replay.read(req.actor_id, uuid4(), command.request_id)


def test_missing_handler_and_v1_decoder_cannot_fall_back_to_network(database, tmp_path):
    req, _, provider, _, repo, _, normal, replay, command, _ = setup(database, tmp_path)
    value = replay.preview(command)
    replay.submit(command, value.control_sha256)
    with psycopg.connect(database) as conn:
        body = conn.execute(
            "SELECT payload FROM qtrade_data.collection_part WHERE scope_id=%s AND part_id=%s",
            (req.scope_id, value.target_part_id),
        ).fetchone()[0]
    with pytest.raises(ValidationError):
        CollectionJobRequest.model_validate_json(json.dumps(body["request"]))
    normal.replays = None
    disable_acquisition(provider)
    normal.run_once()
    result = repo.job(req.scope_id, command.request_id)
    assert result["state"] == "failed" and result["parts"][0]["error_code"] == "unsupported"
    with psycopg.connect(database, autocommit=True) as conn, pytest.raises(psycopg.Error):
        conn.execute(
            "UPDATE qtrade_data.collection_part SET payload=payload-'execution_kind' "
            "WHERE scope_id=%s AND part_id=%s",
            (req.scope_id, value.target_part_id),
        )


def test_atomic_failure_leaves_no_child_and_intent_is_immutable(database, tmp_path):
    req, _, _, _, repo, _, _, replay, command, _ = setup(database, tmp_path)
    value = replay.preview(command)
    body = value.model_dump(mode="json")
    body["input"]["owner_job_id"] = str(uuid4())
    body["control_sha256"] = digest({k: v for k, v in body.items() if k != "control_sha256"})
    invalid = CollectionReplay.model_validate_json(json.dumps(body))
    with pytest.raises(DataFailure, match="unavailable"):
        repo.enqueue_replay(invalid)
    assert (
        repo.find(req.scope_id, command.request_id) is None
        and repo.replay(req.scope_id, command.request_id) is None
    )
    replay.submit(command, value.control_sha256)
    with psycopg.connect(database, autocommit=True) as conn, pytest.raises(psycopg.Error):
        conn.execute(
            "UPDATE qtrade_data.collection_replay SET payload=payload WHERE scope_id=%s",
            (req.scope_id,),
        )
    # Retire only this queued test child, so subsequent claim tests stay isolated.
    repo.control(req.scope_id, command.request_id, "cancel")


def test_expired_replay_lease_cannot_complete_even_without_reclaim(database, tmp_path):
    req, _, _, _, repo, _, _, replay, command, _ = setup(database, tmp_path)
    value = replay.preview(command)
    replay.submit(command, value.control_sha256)
    lease = repo.claim(datetime.now(UTC))
    with psycopg.connect(database) as conn:
        conn.execute(
            "UPDATE qtrade_data.collection_part "
            "SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE scope_id=%s AND part_id=%s",
            (req.scope_id, value.target_part_id),
        )
    with pytest.raises(DataFailure, match="conflict"):
        repo.complete(lease, None)
    repo.fail(lease, "unverified", None)
    assert repo.job(req.scope_id, command.request_id)["parts"][0]["state"] == "running"
    next_lease = repo.claim(datetime.now(UTC))
    assert next_lease.lease_token != lease.lease_token
    repo.complete(next_lease, None)


def test_corrupt_repository_identity_rejected_before_private_archive_reads(database, tmp_path):

    req, parent, _, archive, repo, auth, _, replay, command, _ = setup(database, tmp_path)
    value = replay.preview(command)
    reads = []
    repo.part_for_job = lambda *args: replace(
        parent, request=req.model_copy(update={"scope_id": uuid4()})
    )
    archive.normalization_inputs = lambda *args: reads.append(args)
    with pytest.raises(DataFailure, match="unverified"):
        replay.inputs(req.actor_id, req.scope_id, req.request_id, parent.part_id)
    assert reads == []
    repo.replay = lambda *args: value
    foreign = uuid4()
    auth.grants[f"{req.actor_id}:{foreign}"] = "read"
    with pytest.raises(DataFailure, match="unverified"):
        replay.read(req.actor_id, foreign, command.request_id)


def test_offline_freshness_retains_verified_root_acquisition_and_new_publication(
    database, tmp_path
):
    req, parent, provider, archive, repo, auth, normal, replay, command, calls = setup(
        database, tmp_path, daily=True
    )
    evidence = DailyPublicationEvidence(auth, repo, archive, replays=replay)
    original = repo.publication(req.scope_id, parent.part_id)
    root = evidence.verify(req.actor_id, req.scope_id, original.publication_id)
    count = len(calls)
    disable_acquisition(provider)
    value = replay.preview(command)
    replay.submit(command, value.control_sha256)
    normal.run_once()
    child = repo.publication(req.scope_id, value.target_part_id)
    verified = evidence.verify(req.actor_id, req.scope_id, child.publication_id)
    assert verified.collected.observed_at == root.collected.observed_at
    assert verified.collected.complete_through == root.collected.complete_through
    assert verified.published.observed_at == child.published_at > original.published_at
    assert verified.published.observed_at > verified.collected.observed_at
    child_part = repo.part_for_job(req.scope_id, command.request_id, child.part_id)
    assert archive.source_row_proofs(child_part, child.published_at) == ()
    assert len(calls) == count
    # The READ viewer is independent of the original downloader. Neither a
    # missing replay handler nor a forged lineage may certify freshness.
    viewer = uuid4()
    auth.grants[f"{viewer}:{req.scope_id}"] = "read"
    assert evidence.verify(viewer, req.scope_id, child.publication_id) == verified
    with pytest.raises(DataFailure, match="unverified"):
        DailyPublicationEvidence(auth, repo, archive).verify(
            viewer, req.scope_id, child.publication_id
        )
    corrupted = child.model_copy(
        update={
            "evidence": child.evidence
            | {
                "normalization_replay": child.evidence["normalization_replay"]
                | {"original_acquired_at": [child.published_at.isoformat()]}
            }
        }
    )
    with pytest.raises(DataFailure, match="unverified"):
        replay.proofs(viewer, child_part, corrupted)
    # Frozen root tampering must invalidate a child's read-time evidence, not
    # silently fall back to its newer publication clock or current source data.
    receipt = archive.root / str(req.scope_id) / str(parent.part_id) / "source-captures"
    selected = next(p for p in receipt.glob("payload-*") if p.is_file())
    selected.write_bytes(b"corrupt")
    with pytest.raises(DataFailure, match="unverified"):
        evidence.verify(viewer, req.scope_id, child.publication_id)
    assert len(calls) == count


@pytest.mark.parametrize("rejection", ["revoked", "bytes", "mapping"])
def test_queued_replay_rechecks_authorization_limits_and_mapper(database, tmp_path, rejection):
    req, _, provider, archive, repo, auth, normal, replay, command, calls = setup(
        database, tmp_path
    )
    intent = replay.preview(command)
    replay.submit(command, intent.control_sha256)
    count = len(calls)
    disable_acquisition(provider)
    if rejection == "revoked":
        auth.grants[f"{req.actor_id}:{req.scope_id}"] = "read"
        expected = "forbidden"
    elif rejection == "bytes":
        replay.limits = CollectionReplayLimits(max_bytes=intent.verified_bytes - 1)
        expected = "invalid_input"
    else:
        datasets = provider.datasets
        provider.datasets = lambda: tuple(
            d.model_copy(update={"mapping_version": d.mapping_version + ".changed"})
            for d in datasets()
        )
        expected = "conflict"
    normal.run_once()
    job = repo.job(req.scope_id, command.request_id)
    assert job["state"] == "failed" and job["parts"][0]["error_code"] == expected
    assert job["parts"][0]["publication"] is None and len(calls) == count
    part = repo.part_for_job(req.scope_id, command.request_id, intent.target_part_id)
    assert archive.recover(part) is None
