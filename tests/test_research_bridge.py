"""UI bridge role/identity transport contracts; controlled HTTP, no deployment claim."""

from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from qtrade_api.collector import collector_router
from qtrade_domain.authorization import Permission


def setup(preferences=None):
    actor, scope = uuid4(), uuid4()
    access = SimpleNamespace(user_id=actor, workspace=SimpleNamespace(workspace_id=scope))
    calls = []

    class Security:
        def workspace(self, permission, mutate=False):
            def authorized(role: str = Header("manage")):
                if permission == Permission.DATA_MANAGE and role != "manage":
                    raise HTTPException(403)
                return access

            return authorized

    class Remote:
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return kwargs.get("body") or {"state": "completed"}

    app = FastAPI()
    app.include_router(collector_router(Remote(), Security(), None, None, preferences))
    return TestClient(app), actor, scope, calls


def test_preferences_server_identity_role_and_typed_draft():
    saved = {}

    class Preferences:
        def get(self, actor, scope):
            return saved.get((actor, scope))

        def put(self, actor, scope, draft):
            saved[actor, scope] = draft

    client, actor, scope, calls = setup(Preferences())
    root = f"/api/v1/workspaces/{scope}/data/collector/preferences"
    draft = dict(
        schema_version=1,
        source="mairui.pro.bars",
        granularity="5m",
        first="2025-09-01",
        last="2025-09-05",
        instruments="601288.SH",
        fields=[],
        mode="missing",
    )
    assert client.get(root).json() is None
    assert client.put(root, json=draft, headers={"role": "read"}).status_code == 403
    assert not saved
    assert client.put(root, json=draft).status_code == 200
    result = client.get(root).json()
    assert result["first"] == draft["first"] and result["granularity"] == "5m"
    assert list(saved) == [(actor, scope)] and not calls
    assert client.put(root, json=draft | {"actor_id": str(uuid4())}).status_code == 422
    assert client.put(root, json=draft | {"schema_version": 2}).status_code == 422
    assert client.put(root, json=draft | {"first": "2025-02-30"}).status_code == 422


def test_bridge_freezes_server_identity_and_rejects_cross_scope_and_role():
    client, actor, scope, calls = setup()
    base = f"/api/v1/workspaces/{scope}/data/collector/research"
    selection = dict(
        source="mairui.financial.income",
        kind="financial_statement",
        instrument_ids=["600519.SH"],
        date_axis="report_period",
        start="2015-01-01",
        end="2016-01-01",
    )
    assert (
        client.post(base + "/preview", json=selection, headers={"role": "read"}).status_code == 403
    )
    assert not calls
    result = client.post(base + "/preview", json=selection)
    assert result.status_code == 200
    request = result.json()
    assert request["actor_id"] == str(actor) and request["scope_id"] == str(scope)
    assert calls[-1][2]["actor_id"] == actor and calls[-1][2]["scope_id"] == scope
    assert client.post(base + "/jobs", json=request).status_code == 201
    n = len(calls)
    assert client.post(base + "/jobs", json=request | {"scope_id": str(uuid4())}).status_code == 403
    assert client.post(base + "/jobs", json=request | {"schema_version": 3}).status_code == 422
    assert client.post(base + "/jobs", json=request, headers={"role": "read"}).status_code == 403
    assert len(calls) == n
    assert client.get(base + "/catalog", headers={"role": "read"}).status_code == 200
    assert (
        client.get(base + "/jobs/" + request["request_id"], headers={"role": "read"}).status_code
        == 200
    )


def test_bridge_query_frozen_reference_identity_and_manifest():
    client, actor, scope, calls = setup()
    base = f"/api/v1/workspaces/{scope}/data/collector/research"
    pub = str(uuid4())
    body = dict(
        schema_version=2,
        actor_id=str(actor),
        scope_id=str(scope),
        publication_id=pub,
        content_version="a" * 64,
        kind="financial_statement",
    )
    assert client.post(base + "/query", json=body, headers={"role": "read"}).status_code == 403
    assert not calls
    assert client.post(base + "/query", json=body).status_code == 200
    assert calls[-1][1] == "/v2/research/query"
    n = len(calls)
    assert client.post(base + "/query", json=body | {"actor_id": str(uuid4())}).status_code == 403
    assert len(calls) == n
    assert client.get(base + "/publications/" + pub).status_code == 200


def test_default_range_bridge_forwards_selection_and_server_identity():
    client, actor, scope, calls = setup()
    root = f"/api/v1/workspaces/{scope}/data/collector/default-range"
    selection = {"source": "mairui.history.bars", "granularity": "1d"}
    assert client.get(root, params=selection, headers={"role": "read"}).status_code == 200
    method, path, args = calls[-1]
    assert method == "GET" and path == "/v1/default-range"
    assert args["params"] == selection
    assert args["actor_id"] == actor and args["scope_id"] == scope
    count = len(calls)
    assert client.get(root, params=selection | {"granularity": "invalid"}).status_code == 422
    assert len(calls) == count


def test_quality_bridge_is_read_only_and_preserves_scoped_job_part_path():
    client, actor, scope, calls = setup()
    job, part = uuid4(), uuid4()
    path = f"/api/v1/workspaces/{scope}/data/collector/jobs/{job}/parts/{part}/quality"
    assert client.get(path, headers={"role": "read"}).status_code == 200
    assert calls[-1][1] == f"/v1/jobs/{job}/parts/{part}/quality"
    assert calls[-1][2]["actor_id"] == actor and calls[-1][2]["scope_id"] == scope


def test_collection_explicit_identifiers_win_without_loading_pool():
    client, actor, scope, calls = setup()
    body = dict(
        source="mairui.pro.bars",
        dataset_kind="bars",
        instrument_ids=["600519.SH"],
        snapshot_ids=[str(uuid4())],
        granularity="1d",
        start="2025-01-01T00:00:00+08:00",
        end="2026-01-01T00:00:00+08:00",
        mode="missing",
    )
    root = f"/api/v1/workspaces/{scope}/data/collector"
    result = client.post(root + "/preview", json=body)
    assert result.status_code == 200
    assert result.json()["instrument_ids"] == ["600519.SH"]
    assert calls[-1][1] == "/v1/preview"
    n = len(calls)
    assert (
        client.post(
            root + "/preview", json=body | {"instrument_ids": [], "snapshot_ids": []}
        ).status_code
        == 400
    )
    assert len(calls) == n
