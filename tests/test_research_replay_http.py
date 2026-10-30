"""Real HTTP/PG/Worker with a controlled converter; no live-provider claim."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_research_collection_postgres import setup
from test_research_evidence_postgres import database as isolated_database
from test_research_replay_postgres import command, no_network, replay_service, worker

from qtrade_collector.replay_routes import replay_limits
from qtrade_data_contracts.research_replay import ResearchReplay

database = isolated_database


def test_http_preview_submission_worker_refresh_and_current_auth(database, tmp_path, monkeypatch):
    req, provider, archive, repo, _, plan, executor, auth = setup(database, tmp_path)
    executor.collect(plan, provider)
    original_files = {p: p.read_bytes() for p in archive.root.rglob("*") if p.is_file()}
    provider.fetch = provider.partitions = provider.estimate = no_network
    service = replay_service(database, tmp_path, provider, auth)
    reader = uuid4()
    auth.grants[f"{reader}:{req.scope_id}"] = "read"
    token = "fixture-service-token-" + "x" * 32
    monkeypatch.setenv("COLLECTOR_SERVICE_TOKEN", token)
    monkeypatch.setenv("DATA_COLLECTION_DATABASE_URL", str(database))
    monkeypatch.setenv("COLLECTOR_ARCHIVE_ROOT", str(tmp_path / "app"))
    monkeypatch.setenv("COLLECTOR_SCOPE_GRANTS_JSON", json.dumps(auth.grants))
    monkeypatch.delenv("COLLECTOR_AUTHORIZER_URL", raising=False)
    monkeypatch.delenv("MAIRUI_LICENCE", raising=False)
    from qtrade_collector.main import create_app

    app = create_app()
    # Production composition, not just the route registration, must expose the
    # same bounded service/repository used by its ordinary research queue.
    assert app.state.research_jobs.repository is app.state.research_replays.repository
    app.state.research_replays = service
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer " + token,
        "X-Data-Actor": str(req.actor_id),
        "X-Data-Scope": str(req.scope_id),
    }
    cmd = command(req)
    body = cmd.model_dump(mode="json")
    path = "/v2/research/replays"
    assert client.post(path + "/preview", json=body).status_code == 401
    assert (
        client.post(path + "/preview", headers=headers, json=body | {"max_pages": 1}).status_code
        == 422
    )
    assert (
        client.post(
            path + "/preview", headers=headers, json=body | {"schema_version": 3}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path + "/preview", headers=headers, json=body | {"scope_id": str(uuid4())}
        ).status_code
        == 403
    )
    read_headers = headers | {"X-Data-Actor": str(reader)}
    assert (
        client.post(
            path + "/preview", headers=read_headers, json=body | {"actor_id": str(reader)}
        ).status_code
        == 403
    )
    response = client.post(path + "/preview", headers=headers, json=body)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    value = ResearchReplay.model_validate_json(response.text)
    assert value.command == cmd and value.target_plan.estimated_calls_max == 0
    assert service.repository.jobs(req.scope_id) == ()
    assert client.get(path + f"/{cmd.request_id}", headers=headers).status_code == 404
    assert (
        client.post(path, headers=headers, json=body | {"control_sha256": "0" * 64}).status_code
        == 409
    )
    draft = body | {"control_sha256": value.control_sha256}
    assert (
        client.post(path, headers=read_headers, json=draft | {"actor_id": str(reader)}).status_code
        == 403
    )
    first = client.post(path, headers=headers, json=draft)
    assert first.status_code == 200 and first.json()["state"] == "queued"
    assert first.headers["cache-control"] == "no-store"
    assert client.post(path, headers=headers, json=draft).json() == first.json()
    assert len(service.repository.jobs(req.scope_id)) == 1
    assert worker(service, executor).run_once()
    final = client.get(f"/v2/research/jobs/{cmd.request_id}", headers=headers)
    assert final.status_code == 200 and final.json()["state"] == "completed"
    # The ordinary jobs HTTP surface needs the same persisted queue as replay.
    manifest = repo.manifest(
        req.scope_id, service.repository.job(req.scope_id, cmd.request_id)["publication_id"]
    )
    assert manifest.artifact.record_count == 2
    assert all(archive.raw(b) for b in manifest.artifact.raw_batches)
    frozen = client.get(path + f"/{cmd.request_id}", headers=read_headers)
    assert frozen.status_code == 200 and frozen.headers["cache-control"] == "no-store"
    assert ResearchReplay.model_validate_json(frozen.text) == value
    cross = headers | {"X-Data-Scope": str(uuid4())}
    assert client.get(path + f"/{cmd.request_id}", headers=cross).status_code == 403
    assert all(p.read_bytes() == content for p, content in original_files.items())
    # Exercise the QTrade session boundary through the collector HTTP boundary.
    # Security identity is controlled here; real SSO/CSRF remains a separate UI
    # acceptance requirement, never inferred from this transport fixture.
    from types import SimpleNamespace

    from fastapi import FastAPI, Header, HTTPException

    from qtrade_api.collector import collector_router
    from qtrade_domain.authorization import Permission

    calls = []
    access = SimpleNamespace(
        user_id=req.actor_id, workspace=SimpleNamespace(workspace_id=req.scope_id)
    )

    class Security:
        def workspace(self, permission, mutate=False):
            assert permission != Permission.DATA_MANAGE or mutate

            def identify(role: str = Header("manage")):
                if role == "denied" or (permission == Permission.DATA_MANAGE and role != "manage"):
                    raise HTTPException(403)
                return access

            return identify

    class Remote:
        def request(self, method, route, **kwargs):
            calls.append((method, route, kwargs))
            assert kwargs["actor_id"] == req.actor_id
            assert kwargs["scope_id"] == req.scope_id
            response = client.request(method, route, headers=headers, json=kwargs.get("body"))
            if response.is_error:
                from qtrade_data_core.ports import DataFailure

                raise DataFailure(response.json()["detail"]["code"])
            return response.json()

    bridge_app = FastAPI()
    bridge_app.include_router(collector_router(Remote(), Security(), None, None))
    bridge = TestClient(bridge_app)
    root = f"/api/v1/workspaces/{req.scope_id}/data/collector/research/replays"
    selection = {"request_id": str(cmd.request_id), "parent_request_id": str(req.request_id)}
    bridge_preview = bridge.post(root + "/preview", json=selection)
    assert bridge_preview.status_code == 200
    assert bridge_preview.json() == value.model_dump(mode="json")
    assert bridge_preview.headers["cache-control"] == "no-store"
    accepted = bridge.post(root, json=selection | {"control_sha256": value.control_sha256})
    assert accepted.status_code == 201 and accepted.json()["state"] == "completed"
    assert bridge.get(root + f"/{cmd.request_id}", headers={"role": "read"}).status_code == 200
    assert bridge.get(root + f"/{uuid4()}").status_code == 404
    calls.clear()
    assert (
        bridge.post(root + "/preview", json=selection, headers={"role": "read"}).status_code == 403
    )
    assert (
        bridge.post(
            root,
            json=selection | {"control_sha256": value.control_sha256},
            headers={"role": "read"},
        ).status_code
        == 403
    )
    assert bridge.get(root + f"/{cmd.request_id}", headers={"role": "denied"}).status_code == 403
    assert (
        bridge.post(root + "/preview", json=selection | {"actor_id": str(reader)}).status_code
        == 422
    )
    assert not calls
    assert (
        bridge.post(
            root + "/preview",
            json={"request_id": str(req.request_id), "parent_request_id": str(req.request_id)},
        ).status_code
        == 400
    )
    auth.grants.pop(f"{req.actor_id}:{req.scope_id}")
    assert client.post(path, headers=headers, json=draft).status_code == 403


@pytest.mark.parametrize("key", ["PAGES", "ROWS", "BYTES"])
def test_deployment_bounds_strict_and_safe_error(key):
    name = "COLLECTOR_RESEARCH_REPLAY_MAX_" + key
    assert replay_limits({name: "1"}).model_dump()["max_" + key.lower()] == 1
    for value in ("0", "-1", "true", " 1", "1.0", "9" * 100, "private-config-text"):
        with pytest.raises(ValueError, match="^invalid research replay bounds$"):
            replay_limits({name: value})
