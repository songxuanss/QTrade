"""Service identity and current actor permissions are distinct, with safe failures."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_independent_collection import request

from qtrade_api.collector import collector_authorization_router
from qtrade_domain.authorization import Permission
from qtrade_domain.ports import ErrorCode, PortError


def test_independent_http_identity_scope_role_and_validation(monkeypatch, tmp_path):
    actor, scope = uuid4(), uuid4()
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", "fixture-service-token-" + "x" * 32)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv("COLLECTOR_SCOPE_GRANTS_JSON", json.dumps({f"{actor}:{scope}": "read"}))
    monkeypatch.delenv("COLLECTOR_AUTHORIZER_URL", raising=False)
    monkeypatch.delenv("MAIRUI_LICENCE", raising=False)
    from qtrade_collector.main import create_app

    client = TestClient(create_app())
    headers = {
        "Authorization": "Bearer fixture-service-token-" + "x" * 32,
        "X-Data-Actor": str(actor),
        "X-Data-Scope": str(scope),
    }
    assert client.get("/v1/catalog").status_code == 401
    assert client.get("/v1/catalog", headers=headers).status_code == 200
    assert (
        client.get("/v1/catalog", headers=headers | {"X-Data-Scope": str(uuid4())}).status_code
        == 403
    )
    assert client.get("/v1/factor-catalog").status_code == 401
    assert client.get("/v1/usage").status_code == 401
    assert (
        client.get("/v1/usage", headers=headers | {"X-Data-Scope": str(uuid4())}).status_code == 403
    )
    snapshot = client.get("/v1/factor-catalog", headers=headers)
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "unconfigured"
    assert snapshot.json()["definitions"] == []
    assert (
        client.get(
            "/v1/factor-catalog", headers=headers | {"X-Data-Scope": str(uuid4())}
        ).status_code
        == 403
    )
    from qtrade_data_core.usage_execution import UsageIdentity, usage_identity

    identities = []

    def names(ids):
        identities.append(usage_identity.get())
        return {value: "fixture" for value in ids}

    monkeypatch.setattr(client.app.state.collection_service.providers[0], "names", names)
    assert client.get("/v1/names?instrument_id=601288.SH", headers=headers).status_code == 200
    assert identities == [UsageIdentity(actor, scope)]
    assert usage_identity.get() is None
    req = request(actor_id=actor, scope_id=scope).model_dump(mode="json")
    assert client.post("/v1/preview", headers=headers, json=req).status_code == 403
    response = client.post("/v1/preview", headers=headers, json=req | {"schema_version": 2})
    assert response.status_code == 422 and response.json() == {"detail": {"code": "invalid_input"}}
    assert client.get("/v1/deliveries", params={"consumer_id": "qtrade-market"}).status_code == 401


@pytest.mark.parametrize(
    "name",
    ("COLLECTOR_RUN_WORKER", "COLLECTOR_RUN_SCHEDULER", "COLLECTOR_RUN_FRESHNESS"),
)
@pytest.mark.parametrize("value", ("tru", "TRUE", " true", "true "))
def test_invalid_background_worker_flags_rejected_at_assembly(
    monkeypatch, tmp_path, name, value
):
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", "fixture-service-token-" + "x" * 32)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.delenv("COLLECTOR_CONFIG_FILE", raising=False)
    from qtrade_collector.main import create_app

    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        create_app()


def test_ready_ignores_stale_worker_cycle_when_worker_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", "fixture-service-token-" + "x" * 32)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv("COLLECTOR_RUN_WORKER", "false")
    monkeypatch.delenv("COLLECTOR_CONFIG_FILE", raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    repository = app.state.collection_service.repository
    monkeypatch.setattr(
        repository,
        "_connection",
        lambda: nullcontext(SimpleNamespace(execute=lambda query: None)),
    )
    app.state.worker_last_cycle = 0.0
    monkeypatch.setattr("qtrade_collector.main.monotonic", lambda: 181.0)

    response = TestClient(app).get("/ready")
    assert response.status_code == 200 and response.json() == {"ready": True}


def test_callback_requires_service_identity_and_current_membership():
    calls = []

    def authorize(actor, scope, permission):
        calls.append(permission)
        if scope == denied:
            raise PortError(ErrorCode.REJECTED, "fixture")

    denied = uuid4()
    app = FastAPI()
    app.include_router(
        collector_authorization_router(SimpleNamespace(authorize=authorize), "fixture-token")
    )
    client = TestClient(app)
    body = {"actor_id": str(uuid4()), "scope_id": str(uuid4()), "action": "read"}
    assert client.post("/internal/data/authorize", json=body).status_code == 401
    headers = {"Authorization": "Bearer fixture-token"}
    assert client.post("/internal/data/authorize", headers=headers, json=body).json() == {
        "allowed": True
    }
    assert calls[-1] == Permission.READ
    assert (
        client.post(
            "/internal/data/authorize", headers=headers, json=body | {"action": "manage"}
        ).status_code
        == 200
    )
    assert calls[-1] == Permission.DATA_MANAGE
    assert (
        client.post(
            "/internal/data/authorize", headers=headers, json=body | {"scope_id": str(denied)}
        ).status_code
        == 403
    )
