"""Isolated queue concurrency/restart fences, controlled plans only."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import psycopg
import pytest
from test_research_evidence import evidence
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.research_jobs import PostgresResearchJobs

database = isolated_database


def test_research_control_http_rejects_unconfirmed_and_coerced_ack(database, tmp_path, monkeypatch):
    import json
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from test_research_collection_postgres import setup

    from qtrade_data_core.research_jobs import ResearchJobsService

    req, _, _, _, research, _, _, auth = setup(database, tmp_path)
    research.plan_guard = research.plan_guard.model_copy(update={"confirm_new_parts": 1})
    reader = uuid4()
    auth.grants[f"{reader}:{req.scope_id}"] = "read"
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", "fixture-service-token-" + "x" * 32)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", str(database))
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path / "app"))
    monkeypatch.setenv("COLLECTOR_SCOPE_GRANTS_JSON", json.dumps(auth.grants))
    monkeypatch.delenv("COLLECTOR_AUTHORIZER_URL", raising=False)
    monkeypatch.delenv("MAIRUI_LICENCE", raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    queue = PostgresResearchJobs(database)
    app.state.research_service = research
    app.state.research_jobs = ResearchJobsService(auth, research, queue)
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer fixture-service-token-" + "x" * 32,
        "X-Data-Actor": str(req.actor_id),
        "X-Data-Scope": str(req.scope_id),
    }
    body = req.model_dump(mode="json")
    path = "/v2/research/plan-preview"
    assert client.post(path, json=body).status_code == 401
    assert (
        client.post(path, headers=headers | {"X-Data-Scope": str(uuid4())}, json=body).status_code
        == 403
    )
    assert (
        client.post(path, headers=headers | {"X-Data-Actor": str(reader)}, json=body).status_code
        == 403
    )
    assert (
        client.post(
            path,
            headers=headers | {"X-Data-Actor": str(reader)},
            json=body | {"actor_id": str(reader)},
        ).status_code
        == 403
    )
    preview = client.post(path, headers=headers, json=body)
    assert preview.status_code == 200 and preview.json()["confirmation_required"]
    legacy = client.post("/v2/research/preview", headers=headers, json=body)
    assert legacy.status_code == 200 and "control_sha256" not in legacy.json()
    draft = body | {"control_sha256": preview.json()["control_sha256"]}
    assert client.post("/v2/research/jobs", headers=headers, json=draft).status_code == 400
    for value in [1, "true"]:
        assert (
            client.post(
                "/v2/research/jobs", headers=headers, json=draft | {"confirmed": value}
            ).status_code
            == 422
        )
    assert queue.jobs(req.scope_id) == ()
    assert (
        client.post(
            "/v2/research/jobs", headers=headers, json=draft | {"confirmed": True}
        ).status_code
        == 200
    )
    assert client.post("/v2/research/jobs", headers=headers, json=body).status_code == 200
    with psycopg.connect(database) as conn:
        conn.execute("DELETE FROM qtrade_data.research_job WHERE scope_id=%s", (req.scope_id,))


def test_research_confirmation_rechecks_policy_without_rewriting_frozen_job(database, tmp_path):
    from test_research_collection_postgres import setup

    from qtrade_data_core.research_jobs import ResearchJobsService

    req, _, _, _, research, original, _, auth = setup(database, tmp_path)
    research.plan_guard = research.plan_guard.model_copy(update={"confirm_new_parts": 1})
    queue = PostgresResearchJobs(database)
    jobs = ResearchJobsService(auth, research, queue)
    preview = research.controlled_preview(req)
    assert preview.confirmation_required
    for checksum, confirmed in [(None, True), (preview.control_sha256, False)]:
        with pytest.raises(DataFailure, match="invalid_input"):
            jobs.submit(req, checksum, confirmed)
    assert queue.jobs(req.scope_id) == ()
    research.plan_guard = research.plan_guard.model_copy(update={"confirm_credits": 1})
    with pytest.raises(DataFailure, match="conflict"):
        jobs.submit(req, preview.control_sha256, True)
    current = research.controlled_preview(req)
    accepted = jobs.submit(req, current.control_sha256, True)
    assert queue.plan(req.scope_id, req.request_id) == original
    research.plan_guard = research.plan_guard.model_copy(update={"max_new_parts": 1})
    assert jobs.submit(req) == accepted  # Current authorization, original run.
    with pytest.raises(DataFailure, match="conflict"):
        jobs.submit(req.model_copy(update={"end": req.end.replace(year=req.end.year + 1)}))
    with psycopg.connect(database) as conn:
        conn.execute("DELETE FROM qtrade_data.research_job WHERE scope_id=%s", (req.scope_id,))


def test_research_scale_rejection_leaves_no_run_or_job(database, tmp_path):
    from test_research_collection_postgres import setup

    from qtrade_data_contracts.planning import CollectionPlanGuard
    from qtrade_data_core.research_jobs import ResearchJobsService

    req, provider, _, _, research, _, _, auth = setup(database, tmp_path)
    research.plan_guard = CollectionPlanGuard(
        version="collection-plan-guard.v1",
        max_instruments=1,
        max_new_parts=1,
        confirm_new_parts=100,
        confirm_credits=500,
    )
    repo = PostgresResearchJobs(database)
    jobs = ResearchJobsService(auth, research, repo)
    oversized = req.model_copy(update={"instrument_ids": ("600519.SH", "601288.SH")})
    with pytest.raises(DataFailure, match="invalid_input"):
        jobs.submit(oversized)
    assert provider.fetch_calls == 0 and repo.jobs(req.scope_id) == ()
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.research_run WHERE scope_id=%s", (req.scope_id,)
            ).fetchone()[0]
            == 0
        )
    # The exact one-part cap still produces the same frozen plan and survives
    # repository restart. This proves rejection did not leave partial state.
    assert jobs.submit(req)["state"] == "queued"
    assert len(PostgresResearchJobs(database).jobs(req.scope_id)) == 1
    # Retire only this fixture's queue entry. Leaving a queued run would alter
    # the eligible population of the following competing-claim test.
    with psycopg.connect(database) as conn:
        conn.execute(
            "DELETE FROM qtrade_data.research_job WHERE scope_id=%s AND request_id=%s",
            (req.scope_id, req.request_id),
        )


def test_duplicate_enqueue_and_competing_claims(database, tmp_path):
    _, plan, _, _, _ = evidence(tmp_path)
    repo = PostgresResearchJobs(database)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: repo.enqueue(plan), range(3)))
        claims = list(pool.map(lambda _: PostgresResearchJobs(database).claim(), range(3)))
    claims = [c for c in claims if c]
    assert len(claims) == 1 and claims[0].attempt == 1
    repo.finish(claims[0])
    assert repo.job(plan.request.scope_id, plan.request.request_id)["state"] == "no_observations"
    repo.enqueue(plan)
    assert repo.claim() is None
    with pytest.raises(DataFailure, match="not_found"):
        repo.job(UUID(int=999), plan.request.request_id)
    with pytest.raises(DataFailure, match="conflict"):
        repo.finish(claims[0])


def test_expired_lease_fenced_and_recovery_bounded(database, tmp_path):
    _, plan, _, _, _ = evidence(tmp_path)
    repo = PostgresResearchJobs(database)
    repo.enqueue(plan)
    first = repo.claim(max_attempts=2)

    def expire():
        with psycopg.connect(database) as conn:
            conn.execute(
                "UPDATE qtrade_data.research_job SET lease_until=now()-interval '1 second' "
                "WHERE scope_id=%s AND request_id=%s",
                (plan.request.scope_id, plan.request.request_id),
            )

    expire()
    second = PostgresResearchJobs(database).claim(max_attempts=2)
    assert second.attempt == 2 and second.lease_id != first.lease_id
    with pytest.raises(DataFailure, match="conflict"):
        repo.finish(first, error="unavailable")
    expire()
    assert repo.claim(max_attempts=2) is None
    assert repo.job(plan.request.scope_id, plan.request.request_id)["error_code"] == "timeout"
    with pytest.raises(DataFailure, match="conflict"):
        repo.finish(second)


def test_leased_worker_publishes_nonempty_and_persists_final_result(database, tmp_path):
    from test_research_collection_postgres import setup

    from qtrade_data_core.research_release import ResearchReleaseService
    from qtrade_data_core.research_worker import ResearchWorker

    req, provider, archive, repo, _, plan, executor, auth = setup(database, tmp_path)
    queue = PostgresResearchJobs(database)
    queue.enqueue(plan)
    worker = ResearchWorker(
        queue, executor, ResearchReleaseService(auth, archive, repo), (provider,)
    )
    assert worker.run_once()
    restarted = PostgresResearchJobs(database)
    result = restarted.job(req.scope_id, req.request_id)
    assert result["state"] == "completed"
    manifest = repo.manifest(req.scope_id, result["publication_id"])
    assert manifest.artifact.record_count == 2
    assert all(archive.raw(raw) for raw in manifest.artifact.raw_batches)
    assert not worker.run_once()


def test_scoped_recent_jobs_survive_repository_restart(database, tmp_path):
    _, plan, _, _, _ = evidence(tmp_path)
    repo = PostgresResearchJobs(database)
    repo.enqueue(plan)
    jobs = PostgresResearchJobs(database).jobs(plan.request.scope_id)
    assert len(jobs) == 1 and jobs[0]["request_id"] == plan.request.request_id
    assert jobs[0]["request"]["instrument_ids"] == ["600519.SH"]
    assert repo.jobs(UUID(int=999)) == ()
    with pytest.raises(ValueError):
        repo.jobs(plan.request.scope_id, limit=201)
