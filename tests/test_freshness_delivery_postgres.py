"""Real isolated outbox leases/HTTP; synthetic incidents are not supplier evidence."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from test_freshness_notice import result
from test_freshness_refresh_postgres import setup
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_contracts.freshness_delivery import FreshnessNoticeClaim
from qtrade_data_postgres.freshness_delivery import PostgresFreshnessDelivery

database = isolated_database


def seed(database):
    actor, key, auth, repo, service = setup(database)
    service.commit(actor, result(key))
    service.commit(actor, result(key, 1, status="current"))
    return actor, key, auth, repo


def expire(database, lease):
    # Controlled DB clock fixture, never used against the deployed service.
    with psycopg.connect(database) as conn:
        conn.execute(
            """UPDATE qtrade_data.collection_freshness_delivery
            SET lease_expires_at=clock_timestamp()-interval '1 second',
            available_at=clock_timestamp()-interval '1 second'
            WHERE scope_id=%s AND notice_id=%s AND consumer_id=%s""",
            (lease.scope_id, lease.notice.notice_id, lease.consumer_id),
        )


def test_ordering_recovery_restart_fencing_and_ack_replay(database):
    actor, key, _, _ = seed(database)
    repo = PostgresFreshnessDelivery(database)
    req = FreshnessNoticeClaim(consumer_id="qtrade-alerts")
    (first,) = repo.claim(req)
    assert first.notice.kind == "opened"
    assert repo.claim(req) == ()  # Recovery cannot overtake leased opening.
    assert not repo.acknowledge(
        actor, uuid4(), first.notice.notice_id, req.consumer_id, first.token
    )
    assert not repo.acknowledge(
        uuid4(), key.scope_id, first.notice.notice_id, req.consumer_id, first.token
    )
    assert not repo.acknowledge(
        actor, key.scope_id, first.notice.notice_id, req.consumer_id, uuid4()
    )
    expire(database, first)
    assert not repo.acknowledge(
        actor, key.scope_id, first.notice.notice_id, req.consumer_id, first.token
    )
    (replacement,) = PostgresFreshnessDelivery(database).claim(req)
    assert replacement.notice == first.notice and replacement.token != first.token
    assert not repo.acknowledge(
        actor, key.scope_id, first.notice.notice_id, req.consumer_id, first.token
    )
    assert repo.acknowledge(
        actor, key.scope_id, first.notice.notice_id, req.consumer_id, replacement.token
    )
    assert repo.acknowledge(
        actor, key.scope_id, first.notice.notice_id, req.consumer_id, replacement.token
    )
    (recovery,) = repo.claim(req)
    assert recovery.notice.kind == "recovered"
    assert repo.acknowledge(
        actor, key.scope_id, recovery.notice.notice_id, req.consumer_id, recovery.token
    )
    assert repo.claim(req) == ()


def test_competing_consumers_claim_one_head_and_unrelated_keys_progress(database):
    actor, key, _, _ = seed(database)
    other_actor, other_key, _, _ = seed(database)
    req = FreshnessNoticeClaim(consumer_id="qtrade-alerts", limit=1)
    with ThreadPoolExecutor(max_workers=5) as pool:
        batches = list(pool.map(lambda _: PostgresFreshnessDelivery(database).claim(req), range(8)))
    leases = [v for batch in batches for v in batch]
    assert len(leases) == 2 and len({v.notice.notice_id for v in leases}) == 2
    assert all(v.notice.kind == "opened" for v in leases)
    # A separate configured consumer has an independent cursor/ack ledger.
    other = PostgresFreshnessDelivery(database).claim(FreshnessNoticeClaim(consumer_id="other"))
    target = [v for v in other if v.scope_id in {key.scope_id, other_key.scope_id}]
    assert len(target) == 2 and all(v.notice.kind == "opened" for v in target)
    assert len({v.scope_id for v in other}) == len(other)


def test_service_token_consumer_current_roles_scope_and_fence(database, tmp_path, monkeypatch):
    actor, key, _, _ = seed(database)
    token = "fixture-service-token-" + "x" * 32
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", token)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", database)
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "COLLECTOR_SCOPE_GRANTS_JSON", json.dumps({f"{actor}:{key.scope_id}": "manage"})
    )
    for name in ("COLLECTOR_CONFIG_FILE", "COLLECTOR_AUTHORIZER_URL", "MAIRUI_LICENCE"):
        monkeypatch.delenv(name, raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    client = TestClient(app)
    service = {"Authorization": "Bearer " + token}
    headers = service | {"X-Data-Actor": str(actor), "X-Data-Scope": str(key.scope_id)}
    body = {"consumer_id": "qtrade-alerts"}
    assert client.post("/v1/freshness/notices/claim", json=body).status_code == 401
    assert (
        client.post(
            "/v1/freshness/notices/claim", headers=service, json={"consumer_id": "other"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/freshness/notices/claim", headers=service, json=body | {"limit": True}
        ).status_code
        == 422
    )
    response = client.post("/v1/freshness/notices/claim", headers=service, json=body)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    (lease,) = response.json()
    path = "/v1/freshness/notices/" + lease["notice"]["notice_id"] + "/ack"
    ack = body | {"token": lease["token"]}
    app.state.freshness_delivery.auth.grants[f"{actor}:{key.scope_id}"] = "read"
    assert client.post(path, headers=headers, json=ack).status_code == 403
    app.state.freshness_delivery.auth.grants[f"{actor}:{key.scope_id}"] = "manage"
    assert client.post(path, headers=headers, json=ack | {"token": str(uuid4())}).status_code == 409
    assert (
        client.post(path, headers=headers, json=ack | {"scope_id": str(uuid4())}).status_code == 422
    )
    assert (
        client.post(path, headers=headers | {"X-Data-Scope": str(uuid4())}, json=ack).status_code
        == 403
    )
    response = client.post(path, headers=headers, json=ack)
    assert response.status_code == 200 and response.json() == {"accepted": True}
    assert response.headers["cache-control"] == "no-store"
