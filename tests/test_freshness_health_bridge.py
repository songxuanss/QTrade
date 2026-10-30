"""Workspace session drives health query; caller cannot choose a different actor/scope."""

import json
from datetime import timedelta
from types import SimpleNamespace

from test_collection_freshness import NOW
from test_freshness_bridge import setup
from test_freshness_health import catalog

from qtrade_data_contracts.freshness import FreshnessResult
from qtrade_data_contracts.freshness_health import FreshnessHealthQuery
from qtrade_data_core.freshness_health import FreshnessHealth, HealthBatch, HealthRecord
from qtrade_data_http.client import StaticScopeAuthorizer


def prepare(tmp_path, monkeypatch):
    client, root, state, calls, access, key = setup(tmp_path, monkeypatch)
    snapshot = FreshnessResult.model_validate_json(json.dumps(state["snapshot"]))
    auth = StaticScopeAuthorizer({f"{access.user_id}:{key.scope_id}": "read"})
    repo = SimpleNamespace(
        page=lambda *args: HealthBatch(
            NOW + timedelta(seconds=30),
            (HealthRecord(key, "Fixture", snapshot, None, None, "not_applicable"),),
            None,
        )
    )
    state["health"] = (
        FreshnessHealth(auth, repo, lambda: catalog(key), "qtrade-alerts")
        .read(access.user_id, FreshnessHealthQuery(scope_id=key.scope_id))
        .model_dump(mode="json")
    )
    path = root.split("/publications/")[0] + "/freshness/health"
    return client, path, state, calls, access


def test_read_role_scoped_structured_body_and_no_store(tmp_path, monkeypatch):
    client, path, state, calls, access = prepare(tmp_path, monkeypatch)
    response = client.get(path + "?limit=1")
    assert response.status_code == 200 and response.json() == state["health"]
    assert response.headers["cache-control"] == "no-store"
    assert calls[-1][0:2] == ("POST", "/v1/freshness/health")
    assert calls[-1][2]["body"] == {
        "scope_id": str(access.workspace.workspace_id),
        "limit": 1,
        "after_key": None,
    }
    assert calls[-1][2]["actor_id"] == access.user_id
    calls.clear()
    assert client.get(path, headers={"role": "denied"}).status_code == 403 and not calls


def test_bad_limit_cursor_and_wrong_scope_or_cursor_response_rejected(tmp_path, monkeypatch):
    client, path, state, _, _ = prepare(tmp_path, monkeypatch)
    for query in ("?limit=true", "?limit=101", "?after_key=not-a-hash"):
        assert client.get(path + query).status_code == 422
    key = state["health"]["entries"][0]["key_sha256"]
    assert client.get(path + "?after_key=" + key).status_code == 503
    state["health"]["scope_id"] = "00000000-0000-4000-8000-000000000001"
    assert client.get(path).status_code == 503
