"""Real isolated watch leases/rollback; controlled evidence is not a live provider."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from test_collection_freshness import key
from test_freshness_notice import result
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_contracts.freshness_watch import FreshnessWatchControl
from qtrade_data_core.freshness_refresh import FreshnessRefresh
from qtrade_data_core.freshness_watch import FreshnessWatchRunner
from qtrade_data_core.ports import DataFailure
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_postgres.freshness import PostgresFreshness
from qtrade_data_postgres.freshness_watch import PostgresFreshnessWatches, WatchedFreshness

database = isolated_database


def setup(database):
    actor, k = uuid4(), key()
    store = PostgresFreshnessWatches(database)
    watch = store.create(actor, uuid4(), k, 900)
    auth = StaticScopeAuthorizer({f"{actor}:{k.scope_id}": "manage"})
    return actor, k, store, watch, auth


def enable(actor, store, watch):
    return store.control(
        actor,
        watch.scope_id,
        watch.watch_id,
        FreshnessWatchControl(revision=watch.revision, enabled=True),
    )


def test_disabled_create_duplicate_idempotency_explicit_enable_stop_and_adoption(database):
    actor, k, store, watch, _ = setup(database)
    assert not watch.enabled and store.claim() is None
    assert store.create(actor, uuid4(), k, 60) == watch
    active = enable(actor, store, watch)
    lease = store.claim()
    assert lease and lease.watch == active
    assert store.claim() is None
    disabled = store.control(
        actor,
        k.scope_id,
        watch.watch_id,
        FreshnessWatchControl(revision=active.revision, enabled=False),
    )
    assert not disabled.enabled and not store.finish(lease, "deferred", "unavailable", None)
    replacement = enable(uuid4(), store, disabled)
    assert replacement.actor_id != actor
    assert store.claim().watch.actor_id == replacement.actor_id
    with pytest.raises(DataFailure, match="conflict"):
        enable(actor, store, watch)
    with pytest.raises(DataFailure, match="not_found"):
        store.get(uuid4(), watch.watch_id)
    with psycopg.connect(database) as conn:
        rows = conn.execute(
            "SELECT action FROM qtrade_data.collection_freshness_watch_revision "
            "WHERE scope_id=%s ORDER BY revision",
            (k.scope_id,),
        ).fetchall()
    assert [r[0] for r in rows] == ["created", "enabled", "disabled", "enabled"]


def test_concurrent_claim_is_single_owner_and_expired_owner_is_fenced(database):
    actor, k, store, watch, _ = setup(database)
    enable(actor, store, watch)
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(lambda _: PostgresFreshnessWatches(database).claim(), range(12)))
    leases = [v for v in values if v is not None]
    assert len(leases) == 1
    first = leases[0]
    with psycopg.connect(database) as conn:
        conn.execute(
            "UPDATE qtrade_data.collection_freshness_watch "
            "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE scope_id=%s",
            (k.scope_id,),
        )
    new = store.claim()
    assert new.token != first.token
    assert not store.finish(first, "completed", None, datetime.now(UTC))
    assert store.finish(new, "completed", None, datetime.now(UTC))
    assert store.claim() is None
    saved = store.get(k.scope_id, watch.watch_id)
    assert saved.last_checked_at is not None and saved.next_due_at > saved.updated_at


def test_disabled_in_flight_check_cannot_commit_snapshot_or_incident(database):
    actor, k, store, watch, auth = setup(database)
    active = enable(actor, store, watch)
    lease = store.claim()
    refresh = FreshnessRefresh(auth, WatchedFreshness(store, lease))
    store.control(
        actor,
        k.scope_id,
        watch.watch_id,
        FreshnessWatchControl(revision=active.revision, enabled=False),
    )
    with pytest.raises(DataFailure, match="conflict"):
        refresh.commit(actor, result(k))
    assert PostgresFreshness(database).history(k) == ()
    assert PostgresFreshness(database).notices(k) == ()


def test_expiry_after_append_rolls_back_entire_snapshot_and_outbox(database):
    actor, k, store, watch, _ = setup(database)
    enable(actor, store, watch)
    lease = store.claim()
    with pytest.raises(DataFailure, match="conflict"):
        with WatchedFreshness(store, lease).atomic(k) as transaction:
            value = transaction.append(result(k))
            transaction.notify(actor, value)
            transaction.connection.execute(
                "UPDATE qtrade_data.collection_freshness_watch "
                "SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE scope_id=%s",
                (k.scope_id,),
            )
    assert PostgresFreshness(database).history(k) == ()
    assert PostgresFreshness(database).notices(k) == ()


def test_runner_authorization_revocation_pauses_before_provider_and_keeps_audit(database):
    actor, k, store, watch, auth = setup(database)
    enable(actor, store, watch)
    auth.grants.clear()

    def forbidden_factory(lease):
        raise AssertionError("revoked owner reached supplier")

    assert FreshnessWatchRunner(auth, store, forbidden_factory).run_once() is not None
    saved = store.get(k.scope_id, watch.watch_id)
    assert not saved.enabled and saved.paused_reason == "forbidden"
    assert saved.last_checked_at is None
    assert store.claim() is None


def test_runner_success_and_failure_back_off_without_false_success(database):
    actor, k, store, watch, auth = setup(database)
    enable(actor, store, watch)

    def factory(lease):
        def check(*args, **kwargs):
            assert kwargs["expected_key"] == k
            return FreshnessRefresh(auth, WatchedFreshness(store, lease)).commit(
                actor, result(k, status="unknown")
            )

        return SimpleNamespace(
            latest_publication=lambda *args: watch.reference_publication_id, check=check
        )

    FreshnessWatchRunner(auth, store, factory).run_once()
    saved = store.get(k.scope_id, watch.watch_id)
    assert saved.last_checked_at is not None and saved.last_error_code is None
    assert PostgresFreshness(database).latest(k).status == "unknown"
    assert PostgresFreshness(database).notices(k) == ()
    enable(actor, store, saved)

    def failing_factory(lease):
        raise DataFailure("rate_limited")

    FreshnessWatchRunner(auth, store, failing_factory).run_once()
    failed = store.get(k.scope_id, watch.watch_id)
    assert failed.enabled and failed.last_error_code == "rate_limited"
    assert (
        failed.last_checked_at == saved.last_checked_at and failed.next_due_at > failed.updated_at
    )


def test_http_watch_real_publication_timer_read_role_and_revocation(
    database, tmp_path, monkeypatch
):
    import json
    from datetime import time, timedelta

    from fastapi.testclient import TestClient
    from test_daily_freshness_evidence import evidence

    from qtrade_data_core.default_range import natural_year_range
    from qtrade_data_postgres.jobs import PostgresJobs

    task, pub, _, _, _ = evidence(tmp_path / "archive")
    jobs = PostgresJobs(database)
    jobs.submit(task.job_id, task.request, (task,), (), {})
    claimed = jobs.claim(datetime.now(UTC))
    assert claimed.part_id == task.part_id
    jobs.complete(claimed, pub)
    actor, scope = task.request.actor_id, pub.scope_id
    token = "fixture-service-token-" + "x" * 32
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", token)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", database)
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path / "archive"))
    monkeypatch.setenv("COLLECTOR_SCOPE_GRANTS_JSON", json.dumps({f"{actor}:{scope}": "manage"}))
    for name in ("COLLECTOR_CONFIG_FILE", "COLLECTOR_AUTHORIZER_URL", "MAIRUI_LICENCE"):
        monkeypatch.delenv(name, raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    client = TestClient(app)
    calls = []

    def calendar(source, period, now):
        calls.append(source)
        day = now.date()
        return natural_year_range(
            now,
            (day - timedelta(days=1), day),
            timezone="Asia/Shanghai",
            release_time=time(17, 10),
            basis="controlled watch calendar",
        )

    monkeypatch.setattr(app.state.freshness_check.provider, "default_range", calendar)
    headers = {
        "Authorization": "Bearer " + token,
        "X-Data-Actor": str(actor),
        "X-Data-Scope": str(scope),
    }
    body = {"publication_id": str(pub.publication_id)}
    assert client.post("/v1/freshness/watches", json=body).status_code == 401
    assert (
        client.post(
            "/v1/freshness/watches", headers=headers, json=body | {"actor_id": str(actor)}
        ).status_code
        == 422
    )
    created = client.post("/v1/freshness/watches", headers=headers, json=body)
    assert created.status_code == 200 and created.headers["cache-control"] == "no-store"
    watch = created.json()
    assert watch["enabled"] is False
    path = "/v1/freshness/watches/" + watch["watch_id"]
    assert (
        client.post(
            path + "/control", headers=headers, json={"revision": True, "enabled": True}
        ).status_code
        == 422
    )
    enabled = client.post(
        path + "/control", headers=headers, json={"revision": watch["revision"], "enabled": True}
    )
    assert enabled.status_code == 200
    app.state.freshness_watch_runner.run_once()
    saved = client.get(path, headers=headers).json()
    assert saved["last_checked_at"] is not None and saved["last_error_code"] is None
    assert len(calls) == 2
    latest = PostgresFreshness(database).latest(
        app.state.freshness_check.selection(actor, scope, pub.publication_id)
    )
    assert latest.status == "unknown"
    auth = app.state.freshness_snapshots.auth
    auth.grants[f"{actor}:{scope}"] = "read"
    assert (
        client.get(
            "/v1/freshness/watches/by-publication/" + str(pub.publication_id), headers=headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            path + "/control",
            headers=headers,
            json={"revision": saved["revision"], "enabled": False},
        ).status_code
        == 403
    )
    with psycopg.connect(database) as conn:
        # Eligibility clock fixture; the public interface cannot submit due times.
        conn.execute(
            """WITH due AS MATERIALIZED
            (SELECT clock_timestamp()-interval '1 second' AS at)
            UPDATE qtrade_data.collection_freshness_watch
            SET next_due_at=due.at,payload=jsonb_set(payload,'{next_due_at}',to_jsonb(due.at))
            FROM due WHERE scope_id=%s""",
            (scope,),
        )
    app.state.freshness_watch_runner.run_once()
    paused = client.get(path, headers=headers).json()
    assert not paused["enabled"] and paused["paused_reason"] == "forbidden"
    assert len(calls) == 2
