"""Scoped manifest-derived UI query; fixtures do not establish live UI readiness."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from test_collection_freshness import NOW, evidence, expected, key, point
from test_daily_freshness_evidence import evidence as publication_fixture

import qtrade_api.collector_freshness as bridge
from qtrade_api.collector import collector_router
from qtrade_data_core.freshness import evaluate
from qtrade_data_core.ports import DataFailure
from qtrade_domain.authorization import Permission


def setup(tmp_path, monkeypatch):
    task, pub, _, _, _ = publication_fixture(tmp_path)
    actor = uuid4()
    access = SimpleNamespace(user_id=actor, workspace=SimpleNamespace(workspace_id=pub.scope_id))
    calls = []
    selection = key(scope_id=pub.scope_id, source=pub.source, mapping_version=pub.mapping_version)
    snapshot = evaluate(selection, expected(), evidence(selection, published=point()), NOW)
    state = {
        "publication": pub.model_dump(mode="json"),
        "snapshot": snapshot.model_dump(mode="json"),
    }

    class Security:
        def workspace(self, permission, mutate=False):
            assert permission != Permission.DATA_MANAGE or mutate

            def auth(role: str = Header("read")):
                if role == "denied" or (permission == Permission.DATA_MANAGE and role != "manage"):
                    raise HTTPException(403)
                return access

            return auth

    class Remote:
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path.endswith("/response-receipts"):
                return state["responses"]
            if path == "/v1/source-profiles":
                return state["source_profiles"]
            if path == "/v1/freshness/health":
                return state["health"]
            if path.startswith("/v1/freshness/watches"):
                if "watch" not in state:
                    raise DataFailure("not_found")
                return state["watch"]
            value = state[
                "snapshot"
                if path in {"/v1/freshness/latest", "/v1/freshness/check"}
                else "publication"
            ]
            if isinstance(value, Exception):
                raise value
            return value

    monkeypatch.setattr(
        bridge, "datetime", SimpleNamespace(now=lambda _: NOW + timedelta(seconds=1800))
    )
    app = FastAPI()
    app.include_router(collector_router(Remote(), Security(), None, None))
    root = (
        f"/api/v1/workspaces/{pub.scope_id}/data/collector/publications/"
        f"{pub.publication_id}/freshness"
    )
    return TestClient(app), root, state, calls, access, selection


def test_read_role_manifest_identity_no_store_and_snapshot_expiry(tmp_path, monkeypatch):
    client, root, state, calls, access, selection = setup(tmp_path, monkeypatch)
    response = client.get(root)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    value = response.json()
    assert value["stale"] and value["age_seconds"] == 1800
    assert value["snapshot"]["status"] == "current"  # Historical state preserved.
    query = next(call for call in calls if call[1] == "/v1/freshness/latest")
    assert query[0:2] == ("POST", "/v1/freshness/latest")
    assert query[2]["body"] == selection.model_dump(mode="json")
    assert query[2]["actor_id"] == access.user_id
    assert query[2]["scope_id"] == access.workspace.workspace_id
    calls.clear()
    assert client.get(root, headers={"role": "denied"}).status_code == 403
    assert not calls


@pytest.mark.parametrize(
    "field,value", [("scope_id", str(uuid4())), ("publication_id", str(uuid4()))]
)
def test_wrong_manifest_identity_never_queries_freshness(tmp_path, monkeypatch, field, value):
    client, root, state, calls, *_ = setup(tmp_path, monkeypatch)
    state["publication"][field] = value
    assert client.get(root).status_code == 503
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field,value", [("granularity", "5m"), ("evidence", {"adjustment": "forward"})]
)
def test_unsupported_period_or_adjustment_not_queried(tmp_path, monkeypatch, field, value):
    client, root, state, calls, *_ = setup(tmp_path, monkeypatch)
    state["publication"][field] = value
    assert client.get(root).status_code == 400
    assert len(calls) == 1


def test_mismatched_snapshot_not_returned(tmp_path, monkeypatch):
    client, root, state, *_ = setup(tmp_path, monkeypatch)
    state["snapshot"]["key"]["source"] = "other"
    assert client.get(root).status_code == 503


@pytest.mark.parametrize(
    "code,status", [("not_found", 404), ("forbidden", 403), ("unavailable", 503)]
)
def test_remote_failure_stays_explicit(tmp_path, monkeypatch, code, status):
    client, root, state, *_ = setup(tmp_path, monkeypatch)
    state["snapshot"] = DataFailure(code)
    assert client.get(root).status_code == status


def test_clock_skew_not_converted_to_fresh_age(tmp_path, monkeypatch):
    client, root, *_ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bridge, "datetime", SimpleNamespace(now=lambda _: NOW - timedelta(seconds=1))
    )
    assert client.get(root).json()["clock_skew"] is True


def test_check_requires_manager_and_derives_command_from_manifest(tmp_path, monkeypatch):
    client, root, _, calls, access, _ = setup(tmp_path, monkeypatch)
    assert client.post(root + "/check").status_code == 403
    assert not calls
    response = client.post(root + "/check", headers={"role": "manage"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    method, path, kwargs = next(call for call in calls if call[1] == "/v1/freshness/check")
    assert (method, path) == ("POST", "/v1/freshness/check")
    assert kwargs["body"] == {"publication_id": root.split("/")[-2]}
    assert kwargs["actor_id"] == access.user_id
    assert kwargs["scope_id"] == access.workspace.workspace_id


def test_check_rejects_mismatched_result(tmp_path, monkeypatch):
    client, root, state, *_ = setup(tmp_path, monkeypatch)
    state["snapshot"]["key"]["mapping_version"] = "other.version"
    assert client.post(root + "/check", headers={"role": "manage"}).status_code == 503
