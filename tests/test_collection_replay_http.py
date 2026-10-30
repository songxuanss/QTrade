"""Two real HTTP surfaces/PG/Worker with controlled auth and converter, not live UI."""

import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from test_collection_replay_postgres import setup
from test_research_evidence_postgres import database as isolated_database

from qtrade_api.collector import collector_router
from qtrade_data_core.ports import DataFailure
from qtrade_domain.authorization import Permission

database = isolated_database


def test_routes_identity_metadata_preview_atomic_worker_and_viewer_bridge(
    database, tmp_path, monkeypatch
):
    req, parent, provider, _, repo, auth, normal, replay, cmd, _ = setup(database, tmp_path)
    reader = uuid4()
    auth.grants[f"{reader}:{req.scope_id}"] = "read"
    token = "fixture-token-" + "x" * 32
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", token)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", str(database))
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path / "http-app"))
    monkeypatch.setenv("COLLECTOR_SCOPE_GRANTS_JSON", json.dumps(auth.grants))
    monkeypatch.delenv("COLLECTOR_AUTHORIZER_URL", raising=False)
    monkeypatch.delenv("MAIRUI_LICENCE", raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    assert app.state.collection_service.replays is app.state.collection_replays
    assert app.state.collection_service.repository is app.state.collection_replays.repository
    app.state.collection_service = normal
    app.state.collection_replays = replay
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer " + token,
        "X-Data-Actor": str(req.actor_id),
        "X-Data-Scope": str(req.scope_id),
    }
    path = f"/v2/collection/jobs/{req.request_id}/parts/{parent.part_id}/normalization-inputs"
    assert client.get(path).status_code == 401
    catalog = client.get(path, headers=headers | {"X-Data-Actor": str(reader)})
    assert catalog.status_code == 200 and catalog.headers["cache-control"] == "no-store"
    assert len(catalog.json()["inputs"]) == 1 and catalog.json()["part_id"] == str(parent.part_id)
    assert '"rows":' not in catalog.text and "lease_token" not in catalog.text
    assert client.get(path, headers=headers | {"X-Data-Scope": str(uuid4())}).status_code == 403
    body = cmd.model_dump(mode="json")
    endpoint = "/v2/collection/replays"
    assert (
        client.post(
            endpoint + "/preview", headers=headers, json=body | {"actor_id": str(reader)}
        ).status_code
        == 403
    )
    assert (
        client.post(
            endpoint + "/preview",
            headers=headers | {"X-Data-Actor": str(reader)},
            json=body | {"actor_id": str(reader)},
        ).status_code
        == 403
    )
    assert (
        client.post(endpoint + "/preview", headers=headers, json=body | {"max_rows": 1}).status_code
        == 422
    )
    preview = client.post(endpoint + "/preview", headers=headers, json=body)
    assert preview.status_code == 200 and preview.headers["cache-control"] == "no-store"
    assert '"rows":' not in preview.text and "lease_token" not in preview.text
    assert len(repo.jobs(req.scope_id)) == 1
    control = preview.json()["control_sha256"]
    assert (
        client.post(endpoint, headers=headers, json=body | {"control_sha256": "0" * 64}).status_code
        == 409
    )

    access = SimpleNamespace(
        user_id=req.actor_id, workspace=SimpleNamespace(workspace_id=req.scope_id)
    )
    calls = []

    class Security:
        def workspace(self, permission, mutate=False):
            assert permission != Permission.DATA_MANAGE or mutate

            def identify(role: str = Header("manage")):
                if role == "denied" or (permission == Permission.DATA_MANAGE and role != "manage"):
                    raise HTTPException(403)
                return access

            return identify

    class Remote:
        def request(self, method, path, **kwargs):
            calls.append(path)
            assert kwargs["actor_id"] == req.actor_id and kwargs["scope_id"] == req.scope_id
            result = client.request(method, path, headers=headers, json=kwargs.get("body"))
            if result.is_error:
                raise DataFailure(result.json()["detail"]["code"])
            return result.json()

    bridge_app = FastAPI()
    bridge_app.include_router(collector_router(Remote(), Security(), None, None))
    bridge = TestClient(bridge_app)
    root = f"/api/v1/workspaces/{req.scope_id}/data/collector"
    selected = {
        k: v
        for k, v in body.items()
        if k in {"request_id", "parent_job_id", "parent_part_id", "input_id"}
    }
    assert (
        bridge.get(root + path.removeprefix("/v2/collection"), headers={"role": "read"}).status_code
        == 200
    )
    assert bridge.post(root + "/replays/preview", json=selected).json() == preview.json()
    calls.clear()
    assert (
        bridge.post(root + "/replays/preview", json=selected, headers={"role": "read"}).status_code
        == 403
    )
    assert (
        bridge.post(
            root + "/replays/preview", json=selected | {"actor_id": str(reader)}
        ).status_code
        == 422
    )
    assert not calls
    accepted = bridge.post(root + "/replays", json=selected | {"control_sha256": control})
    assert accepted.status_code == 201 and len(repo.jobs(req.scope_id)) == 2
    assert normal.run_once().job_id == cmd.request_id
    assert repo.job(req.scope_id, cmd.request_id)["state"] == "completed"
    assert (
        bridge.get(root + f"/replays/{cmd.request_id}", headers={"role": "read"}).json()
        == preview.json()
    )
    assert (
        bridge.post(root + "/replays", json=selected | {"control_sha256": control}).status_code
        == 201
    )
    assert len(repo.jobs(req.scope_id)) == 2
