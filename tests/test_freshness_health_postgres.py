"""Actual isolated MVCC reads; no supplier I/O or acknowledgement side effects."""

from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from test_collection_freshness import key
from test_freshness_health import catalog
from test_freshness_notice import result
from test_research_evidence_postgres import database as isolated_database  # noqa: F401

from qtrade_data_contracts.freshness_delivery import FreshnessNoticeClaim
from qtrade_data_contracts.freshness_health import FreshnessHealthQuery
from qtrade_data_core.freshness_health import FreshnessHealth
from qtrade_data_core.freshness_refresh import FreshnessRefresh
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_postgres.freshness import PostgresFreshness
from qtrade_data_postgres.freshness_delivery import PostgresFreshnessDelivery
from qtrade_data_postgres.freshness_health import PostgresFreshnessHealth
from qtrade_data_postgres.freshness_watch import PostgresFreshnessWatches
from qtrade_data_postgres.migrate import apply


@pytest.fixture
def database(request):
    # Delivery claims deliberately scan all scopes. Use an owned database so an
    # unclaimed health-test notice cannot alter another module's queue assertions.
    shared = request.getfixturevalue("isolated_database")
    connection = psycopg.conninfo.conninfo_to_dict(shared)
    name = "research_test_health_" + uuid4().hex
    with psycopg.connect(shared, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    private = type(shared)(psycopg.conninfo.make_conninfo(**(connection | {"dbname": name})))
    try:
        apply(private)
        yield private
    finally:
        with psycopg.connect(shared, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def setup(database, scope=None):
    actor, k = uuid4(), key(scope_id=scope or uuid4())
    auth = StaticScopeAuthorizer({f"{actor}:{k.scope_id}": "manage"})
    return actor, k, auth, PostgresFreshness(database)


def test_scoped_keyset_pages_include_unchecked_watch_and_never_duplicate(database):
    actor, k, auth, repo = setup(database)
    keys = [k]
    for code in ["600519.SH", "600036.SH"]:
        keys.append(k.model_copy(update={"instrument_id": code}))
    for selection in keys[:2]:
        repo.append(result(selection, status="unknown"))
    watch = PostgresFreshnessWatches(database).create(actor, uuid4(), keys[2], 900)
    source = FreshnessHealth(
        auth, PostgresFreshnessHealth(database), lambda: catalog(k), "qtrade-alerts"
    )
    cursor = None
    received = []
    for _ in range(4):
        page = source.read(
            actor, FreshnessHealthQuery(scope_id=k.scope_id, limit=1, after_key=cursor)
        )
        received.extend(page.entries)
        cursor = page.next_key
        if cursor is None:
            break
    assert len(received) == 3 and len({e.key_sha256 for e in received}) == 3
    assert next(e for e in received if e.watch).watch == watch
    assert next(e for e in received if e.watch).effective_status == "unchecked"
    other_actor, other_key, other_auth, _ = setup(database)
    assert (
        FreshnessHealth(
            other_auth, PostgresFreshnessHealth(database), lambda: catalog(k), "qtrade-alerts"
        )
        .read(other_actor, FreshnessHealthQuery(scope_id=other_key.scope_id))
        .entries
        == ()
    )


def test_health_reads_do_not_claim_or_acknowledge_notices(database):
    actor, k, auth, repo = setup(database)
    FreshnessRefresh(auth, repo).commit(actor, result(k))
    health = FreshnessHealth(
        auth, PostgresFreshnessHealth(database), lambda: catalog(k), "qtrade-alerts"
    )
    query = FreshnessHealthQuery(scope_id=k.scope_id)
    assert health.read(actor, query).entries[0].consumer_state == "not_claimed"
    # Distinct test consumer prevents unrelated module fixtures affecting the head.
    consumer = "health-test-" + uuid4().hex
    leases = PostgresFreshnessDelivery(database).claim(
        FreshnessNoticeClaim(consumer_id=consumer, limit=100)
    )
    lease = next(v for v in leases if v.scope_id == k.scope_id)
    health = FreshnessHealth(auth, PostgresFreshnessHealth(database), lambda: catalog(k), consumer)
    assert health.read(actor, query).entries[0].consumer_state == "leased"
    assert health.read(actor, query).entries[0].consumer_state == "leased"
    assert PostgresFreshnessDelivery(database).acknowledge(
        actor, k.scope_id, lease.notice.notice_id, consumer, lease.token
    )
    entry = health.read(actor, query).entries[0]
    assert entry.consumer_state == "delivered" and entry.snapshot.status == "unavailable"


def test_authenticated_http_scope_bounds_and_no_supplier_call(database, tmp_path, monkeypatch):
    import json

    actor, k, _, repo = setup(database)
    repo.append(result(k, status="unknown"))
    token = "fixture-service-token-" + "x" * 32
    for name, value in {
        "COLLECTOR_SERVICE_TOKEN": token,
        "DATA_COLLECTION_DATABASE_URL": database,
        "COLLECTOR_ARCHIVE_ROOT": str(tmp_path),
        "COLLECTOR_SCOPE_GRANTS_JSON": json.dumps({f"{actor}:{k.scope_id}": "read"}),
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("COLLECTOR_CONFIG_FILE", "COLLECTOR_AUTHORIZER_URL", "MAIRUI_LICENCE"):
        monkeypatch.delenv(name, raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    client = TestClient(app)
    provider = app.state.collection_service.providers[0]

    def forbidden(*args, **kwargs):
        raise AssertionError("read triggered supplier")

    monkeypatch.setattr(provider, "default_range", forbidden)
    monkeypatch.setattr(provider, "fetch", forbidden)
    headers = {
        "Authorization": "Bearer " + token,
        "X-Data-Actor": str(actor),
        "X-Data-Scope": str(k.scope_id),
    }
    body = {"scope_id": str(k.scope_id), "limit": 1}
    assert client.post("/v1/freshness/health", json=body).status_code == 401
    response = client.post("/v1/freshness/health", headers=headers, json=body)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["entries"][0]["effective_status"] == "source_unconfigured"
    assert (
        client.post(
            "/v1/freshness/health", headers=headers, json=body | {"scope_id": str(uuid4())}
        ).status_code
        == 403
    )
    for invalid in (
        {"limit": True},
        {"limit": 101},
        {"after_key": "not-a-hash"},
        {"actor_id": str(actor)},
    ):
        assert (
            client.post("/v1/freshness/health", headers=headers, json=body | invalid).status_code
            == 422
        )
    app.state.freshness_health.auth.grants.clear()
    assert client.post("/v1/freshness/health", headers=headers, json=body).status_code == 403
