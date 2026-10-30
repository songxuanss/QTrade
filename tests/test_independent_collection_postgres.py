"""Real separate PostgreSQL acceptance. Skipped only when test database is not supplied."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from legacy_collection_fixture import LegacyCollectionFixture
from test_independent_collection import START, bar, request

from qtrade_data_core.collection import CollectionResult, CollectionService
from qtrade_data_core.ports import DataFailure
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_postgres.jobs import PostgresJobs
from qtrade_data_postgres.migrate import apply


class PrivateDatabaseUrl(str):
    def __repr__(self):
        return "<private test database>"


@pytest.fixture
def storage(tmp_path):
    url = os.getenv("QTRADE_TEST_COLLECTOR_DATABASE_URL")
    if not url:
        pytest.skip("separate collector PostgreSQL not configured")
    url = PrivateDatabaseUrl(url)
    apply(url)
    actor, scope = uuid4(), uuid4()
    auth = StaticScopeAuthorizer({f"{actor}:{scope}": "manage"})
    repository = PostgresJobs(url)
    archive = FileCollectionArchive(tmp_path)
    provider = MaiRuiCollection("fixture-key", pro=True, cache=tmp_path / "cache")
    provider.names = lambda ids: dict.fromkeys(ids, "农业银行")

    def fetch(part, *, capture):
        capture("history", [{"c": "3.25"}])
        return CollectionResult(
            ({"c": "3.25"},),
            (bar().model_copy(update={"timestamp": part.start}),),
            {"volume": "shares", "prices": "CNY", "value": "CNY"},
            "UTC",
            {},
        )

    # Controlled replacement implements the legacy fetch seam only.
    provider = LegacyCollectionFixture(provider)
    provider.fetch = fetch
    service = CollectionService(auth, repository, archive, (provider,))
    yield service, request(actor_id=actor, scope_id=scope), url
    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM qtrade_data.collection_job_part WHERE scope_id=%s", (scope,))
        conn.execute("DELETE FROM qtrade_data.collection_part WHERE scope_id=%s", (scope,))
        conn.execute("DELETE FROM qtrade_data.collection_job WHERE scope_id=%s", (scope,))


def test_source_aware_plans_idempotent_source_and_scope_isolated(storage):
    service, req, url = storage
    preview = service.preview(req)
    assert preview.missing_parts == 1
    job = service.submit(req, preview.coverage_version)
    assert service.submit(req, preview.coverage_version)["job_id"] == job["job_id"]
    assert all(
        datetime.fromisoformat(p["end"]) - datetime.fromisoformat(p["start"]) == timedelta(days=16)
        for p in job["parts"]
    )
    for _ in range(1):
        assert service.run_once() is not None
    assert PostgresJobs(url).job(req.scope_id, req.request_id)["state"] == "completed"
    same = req.model_copy(update={"request_id": uuid4(), "correlation_id": uuid4()})
    repeat = service.preview(same)
    assert repeat.missing_parts == 0 and repeat.completed_parts == 1
    assert service.submit(same, repeat.coverage_version)["counts"]["completed"] == 1
    all_req = same.model_copy(update={"request_id": uuid4(), "mode": "all"})
    assert service.preview(all_req).missing_parts == 1
    other_source = same.model_copy(update={"source": "mairui.history.bars"})
    assert service.preview(other_source).missing_parts == 3
    longer = same.model_copy(update={"end": req.end + timedelta(days=2)})
    assert service.preview(longer).missing_parts == 1
    with pytest.raises(DataFailure, match="not_found"):
        service.repository.job(uuid4(), req.request_id)
    with pytest.raises(DataFailure, match="conflict"):
        service.submit(req.model_copy(update={"mode": "all"}), preview.coverage_version)


def test_credit_denial_waits_without_burning_failures_and_resumes_after_reset(storage, monkeypatch):
    import httpx

    import qtrade_data_core.collection as execution
    import qtrade_data_core.usage_execution as admission
    from qtrade_data_core.usage import UsagePolicy, UsageRequest, UsageResult
    from qtrade_data_core.usage_execution import UsageGate
    from qtrade_data_postgres.usage import PostgresUsageLedger

    service, req, url = storage
    preview = service.preview(req)
    service.submit(req, preview.coverage_version)
    now = datetime.now(UTC)
    clock = [now]

    class ControlledClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(execution, "datetime", ControlledClock)
    monkeypatch.setattr(admission, "datetime", ControlledClock)
    policy = UsagePolicy(100, 1, 4, 2)
    source = "fixture." + uuid4().hex
    ledger = PostgresUsageLedger(url)
    previous = ledger.reserve(
        UsageRequest(uuid4(), source, req.scope_id, "bars.history", 1), policy, now
    )
    ledger.finish(previous, UsageResult("success", 1, 2), now)
    provider = service.providers[0]
    provider.usage = UsageGate(ledger, source, policy)
    calls = []
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: calls.append("outbound") or httpx.Response(200, json=[{"c": "3.25"}])
        )
    )
    successful_fetch = provider.fetch

    def fetch(part, *, capture):
        provider._get("hsstock/history/fixture/5/n")
        return successful_fetch(part, capture=capture)

    provider.fetch = fetch
    with psycopg.connect(url) as conn:
        conn.execute(
            "UPDATE qtrade_data.collection_part SET attempt=7 WHERE scope_id=%s", (req.scope_id,)
        )
    claimed = service.run_once()
    status = service.repository.job(req.scope_id, req.request_id)
    waiting = status["parts"][0]
    assert status["state"] == "waiting"
    assert waiting["state"] == "waiting" and waiting["wait_reason"] == "scope_daily"
    assert waiting["attempt"] == 7 and waiting["publication"] is None and calls == []
    retry = datetime.fromisoformat(waiting["next_attempt_at"])
    assert service.repository.claim(retry - timedelta(microseconds=1)) is None
    with pytest.raises(DataFailure, match="conflict"):
        service.repository.wait_budget(claimed, "scope_daily", retry)
    clock[0] = retry + timedelta(seconds=1)
    assert service.run_once() is not None
    resumed = service.repository.job(req.scope_id, req.request_id)
    assert resumed["state"] == "completed" and calls == ["outbound"]
    assert resumed["parts"][0]["wait_reason"] is None
    snapshot = ledger.snapshot(source, req.scope_id, policy, clock[0])
    assert snapshot.request_count == snapshot.success_count == 1
    assert snapshot.scope_budget.credits_used == 1


def test_budget_wait_explicit_retry_and_cancel_release_reason(storage):
    service, req, _ = storage
    preview = service.preview(req)
    service.submit(req, preview.coverage_version)
    part = service.repository.claim(datetime.now(UTC))
    service.repository.wait_budget(
        part, "scope_concurrency", datetime.now(UTC) + timedelta(seconds=30)
    )
    service.repository.control(req.scope_id, req.request_id, "retry")
    status = service.repository.job(req.scope_id, req.request_id)
    assert status["parts"][0]["state"] == "queued"
    assert status["parts"][0]["wait_reason"] is None
    part = service.repository.claim(datetime.now(UTC))
    service.repository.wait_budget(part, "scope_daily", datetime.now(UTC) + timedelta(days=1))
    service.repository.control(req.scope_id, req.request_id, "cancel")
    assert service.repository.job(req.scope_id, req.request_id)["state"] == "cancelled"


def test_unadmissible_budget_does_not_schedule_infinite_daily_wait(storage):
    service, req, url = storage
    preview = service.preview(req)
    service.submit(req, preview.coverage_version)
    part = service.repository.claim(datetime.now(UTC))
    service.repository.wait_budget(part, "scope_daily", None)
    status = service.repository.job(req.scope_id, req.request_id)
    assert status["state"] == "failed"
    assert status["parts"][0]["wait_reason"] == "scope_daily"
    assert status["parts"][0]["attempt"] == 0
    with psycopg.connect(url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE qtrade_data.collection_part SET error_code=NULL WHERE scope_id=%s",
                (req.scope_id,),
            )
    assert service.repository.claim(datetime.now(UTC) + timedelta(days=30)) is None
    service.repository.control(req.scope_id, req.request_id, "retry")
    assert service.repository.claim(datetime.now(UTC)).attempt == 1


def test_retry_budget_wait_preserves_already_published_partition(storage):
    from qtrade_data_core.usage import UsageDenied, UsagePolicy

    service, req, _ = storage
    req = req.model_copy(update={"end": START + timedelta(days=140)})
    preview = service.preview(req)
    assert preview.missing_parts == 2
    service.submit(req, preview.coverage_version)
    completed = service.run_once()
    published = service.archive.recover(completed)
    assert published is not None and service.archive.records(published)

    def denied(part, *, capture):
        raise UsageDenied("scope_daily").scheduled(UsagePolicy(100, 1, 4, 2), datetime.now(UTC))

    service.providers[0].fetch = denied
    service.run_once()
    status = service.repository.job(req.scope_id, req.request_id)
    assert status["counts"] == {"completed": 1, "waiting": 1}
    service.repository.control(req.scope_id, req.request_id, "retry")
    status = service.repository.job(req.scope_id, req.request_id)
    assert status["counts"] == {"completed": 1, "queued": 1}
    assert service.archive.recover(completed) == published
    assert service.archive.records(published)


def test_plan_limits_reject_before_metadata_and_leave_no_job(storage):
    from qtrade_data_contracts.planning import CollectionPlanGuard

    service, req, _ = storage
    service.plan_guard = CollectionPlanGuard(
        version="collection-plan-guard.v1",
        max_instruments=1,
        max_new_parts=1,
        confirm_new_parts=1,
        confirm_credits=1,
    )
    calls = []
    service.providers[0].names = lambda ids: calls.append(ids) or {}
    oversized = req.model_copy(update={"instrument_ids": ("601288.SH", "600519.SH")})
    with pytest.raises(DataFailure, match="invalid_input"):
        service.preview(oversized)
    assert not calls and service.repository.find(req.scope_id, req.request_id) is None
    longer = req.model_copy(update={"end": START + timedelta(days=140)})
    with pytest.raises(DataFailure, match="invalid_input"):
        service.preview(longer)
    assert service.repository.find(req.scope_id, req.request_id) is None


def test_confirmation_bound_to_current_policy_and_cannot_bypass_hard_limit(storage):
    from qtrade_data_contracts.planning import CollectionPlanGuard

    service, req, _ = storage
    guard = CollectionPlanGuard(
        version="collection-plan-guard.v1",
        max_instruments=2,
        max_new_parts=2,
        confirm_new_parts=1,
        confirm_credits=100,
    )
    service.plan_guard = guard
    preview = service.preview(req)
    assert preview.confirmation_required
    assert preview.execution_estimate.calls_min == 0 and preview.execution_estimate.calls_max >= 1
    assert service.repository.find(req.scope_id, req.request_id) is None
    with pytest.raises(DataFailure, match="invalid_input"):
        service.submit(req, preview.coverage_version)
    assert service.repository.find(req.scope_id, req.request_id) is None
    service.plan_guard = guard.model_copy(update={"confirm_credits": 50})
    with pytest.raises(DataFailure, match="conflict"):
        service.submit(req, preview.coverage_version, True)
    current = service.preview(req)
    created = service.submit(req, current.coverage_version, True)
    assert service.submit(req, current.coverage_version)["job_id"] == created["job_id"]
    oversized = req.model_copy(
        update={"request_id": uuid4(), "instrument_ids": ("601288.SH", "600519.SH", "600036.SH")}
    )
    with pytest.raises(DataFailure, match="invalid_input"):
        service.submit(oversized, current.coverage_version, True)


def test_lease_fencing_restart_recovery_and_retry(storage):
    service, req, url = storage
    req = req.model_copy(update={"end": START + timedelta(days=3)})
    service.submit(req, service.preview(req).coverage_version)
    first = service.repository.claim(datetime.now(UTC))
    assert first and first.attempt == 1
    assert PostgresJobs(url).claim(datetime.now(UTC)) is None
    result = service.providers[0].fetch(
        first, capture=lambda kind, payload: service.archive.capture_raw(first, kind, payload)
    )
    publication = service.archive.publish(first, result)
    with psycopg.connect(url) as conn:
        conn.execute(
            "UPDATE qtrade_data.collection_part SET lease_until=now()-interval '1 second' "
            "WHERE part_id=%s",
            (first.part_id,),
        )
    second = PostgresJobs(url).claim(datetime.now(UTC))
    assert second and second.attempt == 2 and second.lease_token != first.lease_token
    with pytest.raises(DataFailure, match="conflict"):
        service.repository.complete(first, publication)
    assert service.repository.job(req.scope_id, req.request_id)["state"] == "running"
    service.repository.fail(second, "rate_limited", datetime.now(UTC) - timedelta(seconds=1))
    # A new orchestrator/connection recovers durable manifest without calling the provider again.
    service.providers[0].fetch = lambda part, **kwargs: (_ for _ in ()).throw(
        AssertionError("must recover")
    )
    restarted = CollectionService(
        service.auth,
        PostgresJobs(url),
        FileCollectionArchive(service.archive.root),
        service.providers,
    )
    restarted.run_once()
    assert restarted.repository.job(req.scope_id, req.request_id)["state"] == "completed"
    assert restarted.repository.publication(req.scope_id, publication.publication_id) == publication


@pytest.mark.parametrize(
    "status,body,code",
    [
        (401, b"fixture access denied", "unauthorized"),
        (503, b"fixture unavailable", "unavailable"),
        (200, b"{broken", "unavailable"),
    ],
)
def test_worker_http_error_body_survives_without_publication_or_delivery(
    storage, status, body, code
):
    import json
    from base64 import b64decode

    import httpx

    from qtrade_data_contracts.collection import CollectionRawCapture

    service, req, url = storage
    provider = service.providers[0]
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: (
                httpx.Response(200, json=[20250901])
                if "/tcalendar/" in r.url.path
                else httpx.Response(status, content=body)
            )
        )
    )
    provider.fetch = lambda part, *, capture: MaiRuiCollection.fetch(
        provider, part, capture=capture
    )
    preview = service.preview(req)
    service.submit(req, preview.coverage_version)
    claimed = service.run_once()
    stored = service.repository.job(req.scope_id, req.request_id)
    assert stored["parts"][0]["error_code"] == code
    assert stored["parts"][0]["publication"] is None
    directory = service.archive.root / str(req.scope_id) / str(claimed.part_id)
    receipts = [
        CollectionRawCapture.model_validate_json(p.read_bytes())
        for p in (directory / "source-captures").glob("receipt-*.json")
    ]
    envelopes = [
        json.loads(
            (directory / "source-captures" / f"payload-{r.content_sha256}.json").read_bytes()
        )
        for r in receipts
        if r.kind == "http_response"
    ]
    assert any(
        e["http_status"] == status and b64decode(e["body_base64"], validate=True) == body
        for e in envelopes
    )
    assert service.archive.recover(claimed) is None
    assert all(
        str(row["scope_id"]) != str(req.scope_id)
        for row in service.repository.deliveries("fixture-consumer")
    )
    with pytest.raises(DataFailure, match="not_found"):
        service.repository.publication(req.scope_id, claimed.part_id)
    with psycopg.connect(url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.collection_delivery WHERE scope_id=%s",
                (req.scope_id,),
            ).fetchone()[0]
            == 0
        )


def test_http_receipt_alone_cannot_satisfy_quality_or_successful_coverage(storage):
    from qtrade_data_core.collection import CollectionResult

    service, req, _ = storage

    def fetched(part, *, capture):
        capture("http_response", {"http_status": 200, "body_base64": "W10=", "complete": True})
        return CollectionResult(
            ({"c": "3.25"},),
            (bar(),),
            {"prices": "CNY", "volume": "shares", "value": "CNY"},
            "UTC",
            {},
        )

    service.providers[0].fetch = fetched
    preview = service.preview(req)
    service.submit(req, preview.coverage_version)
    claimed = service.run_once()
    stored = service.repository.job(req.scope_id, req.request_id)
    assert stored["state"] == "failed" and stored["parts"][0]["publication"] is None
    quality = service.archive.qualities(claimed)[0]
    assert quality.status == "blocked" and any(i.code == "raw_missing" for i in quality.issues)


def test_normalization_rejection_persists_check_and_scoped_read_after_restart(storage):
    service, req, url = storage

    def rejected(part, *, capture):
        capture("history", [{"t": "bad-time"}])
        raise DataFailure("unverified")

    service.providers[0].fetch = rejected
    service.submit(req, service.preview(req).coverage_version)
    task = service.run_once()
    reports = service.processing_failures(req.actor_id, req.scope_id, req.request_id, task.part_id)
    assert len(reports) == 1 and reports[0].normalized_record_count is None
    assert FileCollectionArchive(service.archive.root).processing_failures(task) == reports
    assert service.repository.job(req.scope_id, req.request_id)["state"] == "failed"
    assert all(
        str(r["scope_id"]) != str(req.scope_id) for r in service.repository.deliveries("fixture")
    )
    with pytest.raises(DataFailure, match="not_found"):
        service.processing_failures(req.actor_id, req.scope_id, uuid4(), task.part_id)
    with pytest.raises(DataFailure, match="forbidden"):
        service.processing_failures(req.actor_id, uuid4(), req.request_id, task.part_id)


def test_check_archive_failure_is_recoverable_not_an_unsaved_rejection(storage, monkeypatch):
    service, req, _ = storage

    def rejected(part, *, capture):
        capture("history", [{"t": "bad-time"}])
        raise DataFailure("unverified")

    def unavailable(*args):
        raise OSError("fixture storage failure")

    service.providers[0].fetch = rejected
    monkeypatch.setattr(service.archive, "record_processing_failure", unavailable)
    service.submit(req, service.preview(req).coverage_version)
    task = service.run_once()
    stored = service.repository.job(req.scope_id, req.request_id)
    assert stored["state"] == "waiting" and stored["parts"][0]["error_code"] == "unavailable"
    assert stored["parts"][0]["publication"] is None
    assert service.archive.processing_failures(task) == ()


def test_quality_lookup_requires_exact_scoped_job_part_link(storage):
    service, req, url = storage
    service.submit(req, service.preview(req).coverage_version)
    task = service.repository.claim(datetime.now(UTC))
    assert task is not None
    assert service.repository.part_for_job(req.scope_id, req.request_id, task.part_id) == task
    for scope, job, part_id in [
        (uuid4(), req.request_id, task.part_id),
        (req.scope_id, uuid4(), task.part_id),
        (req.scope_id, req.request_id, uuid4()),
    ]:
        with pytest.raises(DataFailure, match="not_found"):
            service.repository.part_for_job(scope, job, part_id)


def test_empty_failure_retry_and_revocation(storage):
    service, req, url = storage
    req = req.model_copy(update={"end": START + timedelta(days=3)})

    def empty(part, *, capture):
        capture("history", [])
        return CollectionResult((), (), {}, "UTC", {})

    service.providers[0].fetch = empty
    service.submit(req, service.preview(req).coverage_version)
    service.run_once()
    assert service.repository.job(req.scope_id, req.request_id)["state"] == "no_data"
    service.control(req.actor_id, req.scope_id, req.request_id, "retry")
    service.auth.grants.clear()
    service.run_once()
    job = service.repository.job(req.scope_id, req.request_id)
    assert job["state"] == "failed" and job["parts"][0]["error_code"] == "forbidden"
    assert job["parts"][0]["publication"] is None


def test_preview_conflict_if_another_request_fills_range(storage):
    service, req, url = storage
    preview = service.preview(req)
    other = req.model_copy(update={"request_id": uuid4()})
    service.submit(other, service.preview(other).coverage_version)
    with pytest.raises(DataFailure, match="conflict"):
        service.submit(req, preview.coverage_version)


def test_transient_backoff_and_manual_retry(storage):
    service, req, url = storage
    req = req.model_copy(update={"end": START + timedelta(days=3)})
    service.submit(req, service.preview(req).coverage_version)
    service.providers[0].fetch = lambda part, **kwargs: (_ for _ in ()).throw(
        DataFailure("rate_limited")
    )
    service.run_once()
    job = service.repository.job(req.scope_id, req.request_id)
    assert job["state"] == "waiting" and job["parts"][0]["state"] == "waiting"
    assert job["parts"][0]["error_code"] == "rate_limited"
    assert datetime.fromisoformat(job["parts"][0]["next_attempt_at"]) > datetime.now(UTC)
    assert service.run_once() is None
    service.control(req.actor_id, req.scope_id, req.request_id, "cancel")
    assert service.repository.job(req.scope_id, req.request_id)["state"] == "cancelled"
    service.control(req.actor_id, req.scope_id, req.request_id, "retry")
    assert service.repository.job(req.scope_id, req.request_id)["parts"][0]["state"] == "queued"


def test_frozen_seven_day_plan_survives_policy_change(storage):
    service, req, url = storage
    provider = service.providers[0]
    provider.partition_max_days = 7
    old = service.submit(req, service.preview(req).coverage_version)
    frozen = [(p["part_id"], p["start"], p["end"]) for p in old["parts"]]
    assert len(frozen) == 3
    provider.partition_max_days = 0
    restarted = CollectionService(service.auth, PostgresJobs(url), service.archive, (provider,))
    assert [
        (p["part_id"], p["start"], p["end"])
        for p in restarted.submit(req, "old-preview-not-recomputed")["parts"]
    ] == frozen
    for _ in frozen:
        assert restarted.run_once() is not None
    same = req.model_copy(update={"request_id": uuid4()})
    preview = restarted.preview(same)
    assert preview.missing_parts == 0 and preview.completed_parts == 3
    new = same.model_copy(update={"mode": "all", "request_id": uuid4()})
    assert restarted.preview(new).missing_parts == 1


def test_real_prepared_adapter_worker_keeps_checkpoint_and_published_bytes(storage, tmp_path):
    import httpx

    service, req, _ = storage

    def respond(query):
        if "tcalendar" in query.url.path:
            return httpx.Response(200, json=["20250901"])
        return httpx.Response(
            200, json=[dict(t="2025-09-01 09:35:00", o=10, h=11, l=9, c=10, v=2, a=2000)]
        )

    provider = MaiRuiCollection(
        "fixture",
        pro=True,
        cache=tmp_path / "prepared-cache",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    provider.names = lambda ids: dict.fromkeys(ids, "农业银行")
    service.providers = (provider,)
    preview = service.preview(req)
    service.submit(req, preview.coverage_version)
    claimed = service.run_once()
    final = service.repository.job(req.scope_id, req.request_id)
    assert final["state"] == "completed"
    inputs = service.archive.normalization_inputs(claimed)
    assert len(inputs) == 1 and inputs[0].raw_capture_ids
    publication = service.repository.publication(req.scope_id, claimed.part_id)
    result = provider.normalize(claimed, inputs[0].payload)
    assert service.archive.records(publication) == result.records
    assert service.archive.raw_records(publication) == result.raw
    assert len(result.records) == 1
