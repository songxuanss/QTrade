"""Isolated PG admission competition; controlled requests are not live vendor calls."""

from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from uuid import uuid4

import psycopg
import pytest
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.usage import UsageDenied, UsagePolicy, UsageRequest, UsageResult
from qtrade_data_postgres.usage import PostgresUsageLedger

database = isolated_database
NOW = datetime(2026, 10, 13, 15, 59, tzinfo=UTC)


def request(source=None, scope=None, credits=1):
    return UsageRequest(
        uuid4(), source or "usage-" + uuid4().hex, scope or uuid4(), "bars.history", credits
    )


def compete(arguments):
    url, req, policy = arguments
    try:
        lease = PostgresUsageLedger(url).reserve(req, policy, NOW)
        return "reserved" if lease.newly_reserved else "duplicate"
    except UsageDenied as error:
        return error.reason


def totals(url, req):
    with psycopg.connect(url) as conn:
        return conn.execute(
            "SELECT scope_key,credits,requests FROM qtrade_data.usage_day "
            "WHERE source=%s ORDER BY scope_key",
            (req.source,),
        ).fetchall()


def test_multiprocess_daily_budget_admits_only_available_credit(database):
    req = request(credits=2)
    policy = UsagePolicy(6, 100, 100, 100)
    args = [(str(database), replace(req, request_id=uuid4()), policy) for _ in range(8)]
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
        results = list(pool.map(compete, args))
    assert results.count("reserved") == 3 and results.count("source_daily") == 5
    assert {row[1:] for row in totals(database, req)} == {(6, 3)}


def test_duplicate_processes_charge_once_and_do_not_grant_second_owner(database):
    req = request()
    policy = UsagePolicy(10, 10, 10, 10)
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
        results = list(pool.map(compete, [(str(database), req, policy)] * 4))
    assert results.count("reserved") == 1 and results.count("duplicate") == 3
    assert {row[1:] for row in totals(database, req)} == {(1, 1)}
    duplicate = PostgresUsageLedger(database).reserve(req, policy, NOW)
    with pytest.raises(DataFailure, match="conflict"):
        PostgresUsageLedger(database).finish(duplicate, UsageResult("success", 12, 20), NOW)
    with pytest.raises(DataFailure, match="conflict"):
        PostgresUsageLedger(database).reserve(replace(req, credits=2), policy, NOW)
    with pytest.raises(DataFailure, match="conflict"):
        PostgresUsageLedger(database).reserve(replace(req, source="different"), policy, NOW)


def test_scope_budget_isolated_but_source_shared(database):
    req = request(credits=3)
    policy = UsagePolicy(20, 3, 10, 10)
    repo = PostgresUsageLedger(database)
    first = repo.reserve(req, policy, NOW)
    repo.finish(first, UsageResult("failure", 100, 0, "timeout"), NOW + timedelta(seconds=1))
    with pytest.raises(UsageDenied) as blocked:
        repo.reserve(replace(req, request_id=uuid4()), policy, NOW)
    assert blocked.value.reason == "scope_daily"
    repo.reserve(replace(req, request_id=uuid4(), scope_id=uuid4()), policy, NOW)
    assert next(row for row in totals(database, req) if row[0] == "source")[1:] == (6, 2)


def test_concurrency_expiry_no_refund_and_stale_finish_fence(database):
    req = request()
    policy = UsagePolicy(10, 10, 1, 1, lease_seconds=2)
    repo = PostgresUsageLedger(database)
    lease = repo.reserve(req, policy, NOW)
    with pytest.raises(UsageDenied) as blocked:
        repo.reserve(replace(req, request_id=uuid4(), scope_id=uuid4()), policy, NOW)
    assert blocked.value.reason == "source_concurrency"
    after = NOW + timedelta(seconds=3)
    next_lease = repo.reserve(replace(req, request_id=uuid4()), policy, after)
    with pytest.raises(DataFailure, match="conflict"):
        repo.finish(lease, UsageResult("success", 10, 20), after)
    old = repo.reserve(req, policy, after)
    assert old.state == "expired" and not old.newly_reserved
    assert {row[1:] for row in totals(database, req)} == {(2, 2)}
    repo.finish(next_lease, UsageResult("success", 10, 20), after)


def test_scope_slot_rejection_and_idempotent_completion(database):
    req = request()
    policy = UsagePolicy(20, 20, 3, 1)
    repo = PostgresUsageLedger(database)
    first = repo.reserve(req, policy, NOW)
    with pytest.raises(UsageDenied) as blocked:
        repo.reserve(replace(req, request_id=uuid4()), policy, NOW)
    assert blocked.value.reason == "scope_concurrency"
    result = UsageResult("success", 5, 99)
    with pytest.raises(DataFailure, match="conflict"):
        repo.finish(first, result, NOW - timedelta(seconds=1))
    repo.finish(first, result, NOW)
    repo.finish(first, result, NOW + timedelta(seconds=1))
    with pytest.raises(DataFailure, match="conflict"):
        repo.finish(first, replace(result, response_bytes=100), NOW)
    repo.reserve(replace(req, request_id=uuid4()), policy, NOW)
    assert {row[1:] for row in totals(database, req)} == {(2, 2)}


def test_source_timezone_rollover_does_not_release_active_previous_day_slot(database):
    req = request()
    repo = PostgresUsageLedger(database)
    policy = UsagePolicy(1, 1, 1, 1, lease_seconds=120)
    old = repo.reserve(req, policy, NOW)  # 23:59 Asia/Shanghai
    tomorrow = NOW + timedelta(minutes=1)
    with pytest.raises(UsageDenied) as blocked:
        repo.reserve(replace(req, request_id=uuid4()), policy, tomorrow)
    assert blocked.value.reason == "source_concurrency"
    repo.finish(old, UsageResult("success", 1, 1), tomorrow)
    new = repo.reserve(replace(req, request_id=uuid4()), policy, tomorrow)
    assert new.day == old.day + timedelta(days=1)


def test_background_metadata_charges_source_only_and_null_bypasses_rejected(database):
    req = UsageRequest(uuid4(), "usage-" + uuid4().hex, None, "metadata.catalog", 1)
    repo = PostgresUsageLedger(database)
    lease = repo.reserve(req, UsagePolicy(10, 10, 2, 1), NOW)
    assert totals(database, req) == [("source", 1, 1)]
    for column, value in [("policy", '{"version":null}'), ("outcome", None), ("time", None)]:
        with psycopg.connect(database) as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                if column == "policy":
                    conn.execute(
                        "UPDATE qtrade_data.usage_request SET policy=%s::jsonb WHERE request_id=%s",
                        (value, req.request_id),
                    )
                elif column == "time":
                    conn.execute(
                        "UPDATE qtrade_data.usage_request SET state='finished',finished_at=%s,"
                        "outcome='success',duration_ms=0,response_bytes=0 WHERE request_id=%s",
                        (NOW - timedelta(seconds=1), req.request_id),
                    )
                else:
                    conn.execute(
                        "UPDATE qtrade_data.usage_request SET state='finished',finished_at=%s,"
                        "outcome=NULL,duration_ms=0,response_bytes=0 WHERE request_id=%s",
                        (NOW, req.request_id),
                    )
    repo.finish(lease, UsageResult("success", 10, 20), NOW)


def test_real_ledger_stops_transport_before_second_weighted_request(database, tmp_path):
    import httpx

    from qtrade_data_core.usage_execution import UsageGate, usage_scope
    from qtrade_data_mairui.collection import MaiRuiCollection

    req = request()
    ledger = PostgresUsageLedger(database)
    calls = []
    adapter = MaiRuiCollection(
        "fixture-key",
        pro=True,
        cache=tmp_path,
        usage=UsageGate(ledger, req.source, UsagePolicy(6, 6, 4, 2)),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: calls.append("transport") or httpx.Response(200, content=b"[]")
            )
        ),
    )
    with usage_scope(uuid4(), req.scope_id):
        assert adapter._get("factor/history/pe_ttm/600519") == []
        with pytest.raises(UsageDenied):
            adapter._get("factor/history/pe_ttm/600519")
    assert calls == ["transport"]
    assert {row[1:] for row in totals(database, req)} == {(5, 1)}
    with psycopg.connect(database) as conn:
        row = conn.execute(
            "SELECT scope_id,operation,credits,state,outcome,response_bytes "
            "FROM qtrade_data.usage_request WHERE source=%s",
            (req.source,),
        ).fetchone()
    assert row == (req.scope_id, "factors.history", 5, "finished", "success", 2)


def test_scoped_snapshot_denial_persistence_expiry_and_no_read_side_effect(database):
    req = request(credits=2)
    other = replace(req, request_id=uuid4(), scope_id=uuid4(), credits=1)
    policy = UsagePolicy(20, 2, 10, 10, lease_seconds=120)
    repo = PostgresUsageLedger(database)
    lease = repo.reserve(req, policy, NOW)
    repo.finish(lease, UsageResult("failure", 20, 14, "timeout"), NOW)
    repo.reserve(other, policy, NOW)
    denied = replace(req, request_id=uuid4())
    for _ in range(2):
        with pytest.raises(UsageDenied):
            repo.reserve(denied, policy, NOW)
    first = repo.snapshot(req.source, req.scope_id, policy, NOW)
    assert first.source_budget.credits_used == 3 and first.scope_budget.credits_used == 2
    assert first.source_budget.active_reservations == 1
    assert first.scope_budget.active_reservations == 0
    assert first.request_count == first.failure_count == first.denial_count == 1
    assert first.response_bytes == 14
    assert [r.request_id for r in first.recent_requests] == [req.request_id]
    assert [r.request_id for r in first.recent_denials] == [denied.request_id]
    assert first.recent_denials[0].reason == "scope_daily"
    assert str(other.scope_id) not in first.model_dump_json()
    assert repo.snapshot(req.source, req.scope_id, policy, NOW) == first
    unknown = repo.snapshot(req.source, uuid4(), policy, NOW)
    assert unknown.request_count == unknown.denial_count == unknown.scope_budget.credits_used == 0
    assert unknown.recent_requests == unknown.recent_denials == ()
    late = repo.snapshot(req.source, other.scope_id, policy, NOW + timedelta(seconds=121))
    # 23:59 -> next day: daily counts reset, but credit history never refunded.
    assert late.request_count == 0 and late.source_budget.active_reservations == 0
    previous = repo.snapshot(req.source, other.scope_id, policy, NOW + timedelta(seconds=30))
    assert previous.request_count == 1
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT state FROM qtrade_data.usage_request WHERE request_id=%s", (other.request_id,)
        ).fetchone() == ("reserved",)


def test_snapshot_limit_totals_include_more_than_recent_fifty(database):
    req = request()
    policy = UsagePolicy(1000, 1000, 1000, 1000)
    repo = PostgresUsageLedger(database)
    for _ in range(55):
        item = replace(req, request_id=uuid4())
        lease = repo.reserve(item, policy, NOW)
        repo.finish(lease, UsageResult("success", 0, 2), NOW)
    snapshot = repo.snapshot(req.source, req.scope_id, policy, NOW)
    assert snapshot.request_count == snapshot.success_count == 55
    assert len(snapshot.recent_requests) == 50 and snapshot.response_bytes == 110
    assert snapshot.scope_budget.credits_used == 55


def test_http_usage_reads_current_scope_with_real_persistence(database, monkeypatch, tmp_path):
    import json

    from fastapi.testclient import TestClient

    actor, scope, other = uuid4(), uuid4(), uuid4()
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", "fixture-service-token-" + "x" * 32)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", str(database))
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv("COLLECTOR_SCOPE_GRANTS_JSON", json.dumps({f"{actor}:{scope}": "read"}))
    monkeypatch.delenv("COLLECTOR_AUTHORIZER_URL", raising=False)
    monkeypatch.delenv("MAIRUI_LICENCE", raising=False)
    from qtrade_collector.main import create_app

    client = TestClient(create_app())
    now = datetime.now(UTC)
    req = request(source="mairui", scope=scope)
    repo = client.app.state.usage_ledger
    repo.reserve(req, UsagePolicy(10000, 2000, 4, 2), now)
    headers = {
        "Authorization": "Bearer fixture-service-token-" + "x" * 32,
        "X-Data-Actor": str(actor),
        "X-Data-Scope": str(scope),
    }
    response = client.get("/v1/usage", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["scope_id"] == str(scope) and body["request_count"] == 1
    assert body["recent_requests"][0]["request_id"] == str(req.request_id)
    assert "token" not in json.dumps(body) and "policy" not in body
    assert (
        client.get("/v1/usage", headers=headers | {"X-Data-Scope": str(other)}).status_code == 403
    )
    assert client.get("/v1/usage").status_code == 401
    assert client.get(f"/v1/usage?scope_id={other}", headers=headers).json()["scope_id"] == str(
        scope
    )
