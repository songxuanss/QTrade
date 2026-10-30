"""Authenticated HTTP reads through real isolated PG; no supplier/producer claim."""

import json
from uuid import uuid4

from fastapi.testclient import TestClient
from test_collection_freshness import NOW, evidence, expected, key, point
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_contracts.freshness import FreshnessResult
from qtrade_data_core.freshness import evaluate
from qtrade_data_postgres.freshness import PostgresFreshness

database = isolated_database


def test_actual_subject_read_scope_bounds_and_revocation(database, tmp_path, monkeypatch):
    actor, k = uuid4(), key()
    token = "fixture-service-token-" + "x" * 32
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", token)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", database)
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv("COLLECTOR_SCOPE_GRANTS_JSON", json.dumps({f"{actor}:{k.scope_id}": "read"}))
    for name in ("COLLECTOR_CONFIG_FILE", "COLLECTOR_AUTHORIZER_URL", "MAIRUI_LICENCE"):
        monkeypatch.delenv(name, raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer " + token,
        "X-Data-Actor": str(actor),
        "X-Data-Scope": str(k.scope_id),
    }
    payload = k.model_dump(mode="json")
    result = evaluate(k, expected(), evidence(k, source=point()), NOW)
    PostgresFreshness(database).append(result)

    # These endpoints read a checked_at snapshot, not a live probe. A read must
    # not unexpectedly spend provider credits or make a network request.
    def forbidden_supplier_call(*args, **kwargs):
        raise AssertionError("snapshot query called the supplier")

    provider = app.state.collection_service.providers[0]
    monkeypatch.setattr(provider, "default_range", forbidden_supplier_call)
    monkeypatch.setattr(provider, "fetch", forbidden_supplier_call)
    response = client.post("/v1/freshness/latest", headers=headers, json=payload)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert FreshnessResult.model_validate_json(response.text) == result
    history = client.post(
        "/v1/freshness/history", headers=headers, json={"key": payload, "limit": 1}
    )
    assert history.status_code == 200 and history.headers["cache-control"] == "no-store"
    assert len(history.json()) == 1
    assert (
        client.post(
            "/v1/freshness/history",
            headers=headers,
            json={"key": payload, "before": NOW.isoformat()},
        ).json()
        == []
    )
    assert client.post("/v1/freshness/latest", json=payload).status_code == 401
    assert (
        client.post(
            "/v1/freshness/latest",
            headers=headers | {"Authorization": "Bearer wrong"},
            json=payload,
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/freshness/latest", headers=headers, json=payload | {"scope_id": str(uuid4())}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/freshness/latest", headers=headers | {"X-Data-Scope": str(uuid4())}, json=payload
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/freshness/latest", headers=headers, json=payload | {"actor_id": str(actor)}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/freshness/latest", headers=headers, json=payload | {"source": "missing"}
        ).status_code
        == 404
    )
    for invalid in (
        {"key": payload, "limit": True},
        {"key": payload, "limit": 101},
        {"key": payload, "before": "2026-10-14T10:00:00"},
    ):
        assert (
            client.post("/v1/freshness/history", headers=headers, json=invalid).status_code == 422
        )
    assert (
        client.post(
            "/v1/freshness", headers=headers, json=result.model_dump(mode="json")
        ).status_code
        == 404
    )
    app.state.freshness_snapshots.auth.grants.clear()
    assert client.post("/v1/freshness/latest", headers=headers, json=payload).status_code == 403
    assert (
        client.post("/v1/freshness/history", headers=headers, json={"key": payload}).status_code
        == 403
    )


def test_manager_check_verifies_files_and_persists_only_server_evidence(
    database, tmp_path, monkeypatch
):
    from test_freshness_check import setup

    from qtrade_data_core.freshness_refresh import FreshnessRefresh

    task, pub, checker, calls, *_ = setup(tmp_path / "evidence")
    actor, scope = task.request.actor_id, pub.scope_id
    token = "fixture-service-token-" + "x" * 32
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", token)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", database)
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path / "service"))
    monkeypatch.setenv("COLLECTOR_SCOPE_GRANTS_JSON", json.dumps({f"{actor}:{scope}": "manage"}))
    for name in ("COLLECTOR_CONFIG_FILE", "COLLECTOR_AUTHORIZER_URL", "MAIRUI_LICENCE"):
        monkeypatch.delenv(name, raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    # Controlled publication and calendar, real archive verifier, HTTP subject,
    # authorizer, transactional merge and isolated PostgreSQL persistence.
    checker.auth = app.state.freshness_snapshots.auth
    checker.refresh = FreshnessRefresh(checker.auth, PostgresFreshness(database))
    app.state.freshness_check = checker
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer " + token,
        "X-Data-Actor": str(actor),
        "X-Data-Scope": str(scope),
    }
    body = {"publication_id": str(pub.publication_id)}
    assert client.post("/v1/freshness/check", json=body).status_code == 401
    assert (
        client.post(
            "/v1/freshness/check", headers=headers | {"X-Data-Scope": str(uuid4())}, json=body
        ).status_code
        == 403
    )
    for extra in (
        {"scope_id": str(scope)},
        {"source_health": "available"},
        {"checked_at": NOW.isoformat()},
    ):
        assert (
            client.post("/v1/freshness/check", headers=headers, json=body | extra).status_code
            == 422
        )
    assert not calls
    response = client.post("/v1/freshness/check", headers=headers, json=body)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    result = FreshnessResult.model_validate_json(response.text)
    assert result.status == "unknown" and result.evidence.source is None
    assert len(calls) == 2
    assert PostgresFreshness(database).latest(result.key) == result
    assert (
        client.post(
            "/v1/freshness/latest", headers=headers, json=result.key.model_dump(mode="json")
        ).json()
        == response.json()
    )
    checker.auth.grants[f"{actor}:{scope}"] = "read"
    assert client.post("/v1/freshness/check", headers=headers, json=body).status_code == 403
    checker.auth.grants.clear()
    assert client.post("/v1/freshness/check", headers=headers, json=body).status_code == 403
    assert len(calls) == 2
    assert len(PostgresFreshness(database).history(result.key)) == 1
