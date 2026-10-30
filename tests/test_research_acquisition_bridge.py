"""Session-derived bounded receipt queries; public output never includes bodies."""

from uuid import uuid4

from test_freshness_bridge import setup

from qtrade_data_contracts.research_acquisition import ResearchResponsePage


def test_read_session_scope_limit_params_and_denial(tmp_path, monkeypatch):
    client, root, state, calls, access, _ = setup(tmp_path, monkeypatch)
    job = uuid4()
    state["responses"] = ResearchResponsePage(
        scope_id=access.workspace.workspace_id, request_id=job, receipts=(), next_id=None
    ).model_dump(mode="json")
    path = root.split("/publications/")[0] + f"/research/jobs/{job}/response-receipts"
    response = client.get(path + "?limit=1")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert calls[-1][0:2] == ("GET", f"/v2/research/jobs/{job}/response-receipts")
    assert calls[-1][2]["actor_id"] == access.user_id
    assert calls[-1][2]["scope_id"] == access.workspace.workspace_id
    assert calls[-1][2]["params"] == {"limit": "1"}
    calls.clear()
    assert client.get(path, headers={"role": "denied"}).status_code == 403 and not calls
    for query in ("?limit=true", "?limit=101", "?after_id=not-a-uuid"):
        assert client.get(path + query).status_code == 422
    state["responses"]["scope_id"] = str(uuid4())
    assert client.get(path).status_code == 503
    state["responses"]["scope_id"] = str(access.workspace.workspace_id)
    state["responses"]["request_id"] = str(uuid4())
    assert client.get(path).status_code == 503
