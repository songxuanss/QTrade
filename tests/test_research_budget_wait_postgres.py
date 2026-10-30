"""Isolated worker/credit/wait/restart evidence with a controlled paged source."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import psycopg
import pytest
from test_fair_dispatch_postgres import database as isolated_database
from test_fair_dispatch_postgres import root_database as root_fixture
from test_research_collection_postgres import setup

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research_release import ResearchReleaseService
from qtrade_data_core.research_worker import ResearchWorker
from qtrade_data_core.usage import UsagePolicy
from qtrade_data_core.usage_execution import UsageGate
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_postgres.research_jobs import PostgresResearchJobs
from qtrade_data_postgres.usage import PostgresUsageLedger

database, root_database = isolated_database, root_fixture


def ready(url, req):
    with psycopg.connect(url) as conn:
        conn.execute(
            "UPDATE qtrade_data.research_job SET next_attempt_at=now()-interval '1 second' "
            "WHERE scope_id=%s AND request_id=%s",
            (req.scope_id, req.request_id),
        )


def test_budget_mid_paging_retains_raw_then_restarts_and_publishes(database, tmp_path):
    req, provider, archive, repo, research, plan, executor, auth = setup(database, tmp_path)
    queue = PostgresResearchJobs(database)
    from qtrade_data_core.research_jobs import ResearchJobsService

    research.plan_guard = research.plan_guard.model_copy(update={"confirm_new_parts": 1})
    preview = research.controlled_preview(req)
    assert preview.confirmation_required
    ResearchJobsService(auth, research, queue).submit(req, preview.control_sha256, True)
    assert queue.plan(req.scope_id, req.request_id) == plan
    ledger, source, calls = PostgresUsageLedger(database), "fixture." + uuid4().hex, []
    adapter = MaiRuiCollection(
        "fixture-key",
        cache=tmp_path / "http-cache",
        usage=UsageGate(ledger, source, UsagePolicy(100, 1, 4, 2)),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: calls.append("outbound") or httpx.Response(200, json={"amount": "0"})
            )
        ),
    )
    fetch = provider.fetch

    def fetch_with_admission(request, partition, cursor):
        adapter._get("fixture/financial/data")
        return fetch(request, partition, cursor)

    provider.fetch = fetch_with_admission
    worker = ResearchWorker(
        queue, executor, ResearchReleaseService(auth, archive, repo), (provider,)
    )
    assert worker.run_once()
    status = PostgresResearchJobs(database).job(req.scope_id, req.request_id)
    assert status["state"] == "waiting" and status["wait_reason"] == "scope_daily"
    assert status["attempts"] == 0 and status["publication_id"] is None
    assert calls == ["outbound"] and not worker.run_once()
    with psycopg.connect(database) as conn:
        before = conn.execute(
            "SELECT batch_id,manifest FROM qtrade_data.research_raw_batch WHERE scope_id=%s",
            (req.scope_id,),
        ).fetchall()
    assert len(before) == 1
    adapter.usage = UsageGate(ledger, source, UsagePolicy(100, 10, 4, 2))
    queue.retry(req.scope_id, req.request_id)  # Authorized service is tested separately below.
    restarted = ResearchWorker(
        PostgresResearchJobs(database),
        executor,
        ResearchReleaseService(auth, archive, repo),
        (provider,),
    )
    assert restarted.run_once()
    status = queue.job(req.scope_id, req.request_id)
    assert status["state"] == "completed" and status["wait_reason"] is None
    assert status["attempts"] == 1 and len(calls) == 3
    manifest = repo.manifest(req.scope_id, status["publication_id"])
    assert manifest.artifact.record_count == 2
    assert all(archive.raw(raw) for raw in manifest.artifact.raw_batches)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT manifest FROM qtrade_data.research_raw_batch WHERE scope_id=%s AND batch_id=%s",
            (req.scope_id, before[0][0]),
        ).fetchone() == (before[0][1],)
    snapshot = ledger.snapshot(source, req.scope_id, UsagePolicy(100, 10, 4, 2), datetime.now(UTC))
    assert snapshot.request_count == snapshot.success_count == 3
    assert snapshot.scope_budget.credits_used == 3 and snapshot.denial_count == 1


def test_wait_restart_rechecks_current_authorization_and_fences_old_owner(database, tmp_path):
    req, provider, archive, repo, _, plan, executor, auth = setup(database, tmp_path)
    queue = PostgresResearchJobs(database)
    queue.enqueue(plan)
    lease = queue.claim()
    queue.wait_budget(lease, "scope_daily", datetime.now(UTC) + timedelta(days=1))
    assert PostgresResearchJobs(database).claim() is None
    with pytest.raises(DataFailure, match="conflict"):
        queue.finish(lease)
    with pytest.raises(DataFailure, match="conflict"):
        queue.wait_budget(lease, "scope_daily", None)
    auth.grants.clear()
    ready(database, req)
    worker = ResearchWorker(
        PostgresResearchJobs(database),
        executor,
        ResearchReleaseService(auth, archive, repo),
        (provider,),
    )
    assert worker.run_once()
    state = queue.job(req.scope_id, req.request_id)
    assert state["state"] == "failed" and state["error_code"] == "forbidden"
    assert state["wait_reason"] is None and provider.fetch_calls == 0


def test_no_retry_budget_is_failed_and_null_status_rejected(database, tmp_path):
    req, _, _, _, _, plan, _, _ = setup(database, tmp_path)
    queue = PostgresResearchJobs(database)
    queue.enqueue(plan)
    lease = queue.claim()
    queue.wait_budget(lease, "scope_daily", None)
    assert queue.job(req.scope_id, req.request_id)["state"] == "failed"
    assert queue.claim() is None
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE qtrade_data.research_job SET error_code=NULL WHERE scope_id=%s",
                (req.scope_id,),
            )


def test_retry_does_not_replace_running_owner_or_completed_publication(database, tmp_path):
    req, provider, archive, repo, _, plan, executor, auth = setup(database, tmp_path)
    queue = PostgresResearchJobs(database)
    queue.enqueue(plan)
    lease = queue.claim()
    queue.retry(req.scope_id, req.request_id)
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT lease_id FROM qtrade_data.research_job WHERE scope_id=%s AND request_id=%s",
            (req.scope_id, req.request_id),
        ).fetchone() == (lease.lease_id,)
    queue.wait_budget(lease, "scope_daily", datetime.now(UTC) + timedelta(days=1))
    queue.retry(req.scope_id, req.request_id)
    queue.retry(req.scope_id, req.request_id)
    worker = ResearchWorker(
        queue, executor, ResearchReleaseService(auth, archive, repo), (provider,)
    )
    assert worker.run_once()
    before = queue.job(req.scope_id, req.request_id)
    manifest = repo.manifest(req.scope_id, before["publication_id"])
    queue.retry(req.scope_id, req.request_id)
    assert queue.job(req.scope_id, req.request_id) == before
    assert repo.manifest(req.scope_id, before["publication_id"]) == manifest
    assert not worker.run_once()
    with pytest.raises(DataFailure, match="not_found"):
        queue.retry(uuid4(), req.request_id)


def test_http_retry_manage_read_role_and_current_scope(database, tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from qtrade_data_core.research_jobs import ResearchJobsService

    req, _, _, _, research, plan, _, auth = setup(database, tmp_path)
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
    queue.enqueue(plan)
    lease = queue.claim()
    queue.wait_budget(lease, "scope_daily", datetime.now(UTC) + timedelta(days=1))
    app.state.research_jobs = ResearchJobsService(auth, research, queue)
    client = TestClient(app)
    path = f"/v2/research/jobs/{req.request_id}/retry"
    headers = {
        "Authorization": "Bearer fixture-service-token-" + "x" * 32,
        "X-Data-Actor": str(req.actor_id),
        "X-Data-Scope": str(req.scope_id),
    }
    assert client.post(path).status_code == 401
    assert client.post(path, headers=headers | {"X-Data-Actor": str(reader)}).status_code == 403
    assert client.post(path, headers=headers | {"X-Data-Scope": str(uuid4())}).status_code == 403
    assert queue.job(req.scope_id, req.request_id)["state"] == "waiting"
    assert client.post(path, headers=headers).json()["state"] == "queued"
    assert client.post(path, headers=headers).json()["state"] == "queued"
    assert queue.plan(req.scope_id, req.request_id).request.actor_id == req.actor_id
