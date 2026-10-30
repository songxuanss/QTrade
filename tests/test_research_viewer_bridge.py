"""Current READ viewer queries another creator's publication without impersonation."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from test_research_contracts import statement

from qtrade_api.collector import collector_router
from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.research_transport import ResearchRecordPage
from qtrade_domain.authorization import Permission


def setup():
    viewer, creator, scope, publication = (uuid4() for _ in range(4))
    access = SimpleNamespace(user_id=viewer, workspace=SimpleNamespace(workspace_id=scope))
    calls = []
    records = (statement(scope_id=scope),)
    page = ResearchRecordPage(
        scope_id=scope,
        publication_id=publication,
        content_version="a" * 64,
        kind="financial_statement",
        cursor=None,
        next_cursor=None,
        records=records,
        page_sha256=digest([r.model_dump(mode="json") for r in records]),
    )
    state = {"response": page.model_dump(mode="json")}

    class Security:
        def workspace(self, permission, mutate=False):
            assert permission != Permission.DATA_MANAGE or mutate

            def identify(role: str = Header("read")):
                if role == "denied":
                    raise HTTPException(403)
                return access

            return identify

    class Remote:
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return state["response"]

    app = FastAPI()
    app.include_router(collector_router(Remote(), Security(), None, None))
    path = f"/api/v1/workspaces/{scope}/data/collector/research/records"
    body = dict(
        publication_id=str(publication),
        content_version="a" * 64,
        kind="financial_statement",
        limit=1,
    )
    return TestClient(app), path, body, state, calls, viewer, creator, scope


def test_read_viewer_not_original_creator_and_identity_injection_rejected():
    client, path, body, _, calls, viewer, creator, scope = setup()
    result = client.post(path, json=body)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert len(result.json()["records"]) == 1
    assert calls[-1][0:2] == ("POST", "/v2/research/query")
    sent = calls[-1][2]
    assert sent["actor_id"] == viewer and sent["scope_id"] == scope
    assert sent["body"]["actor_id"] == str(viewer) != str(creator)
    assert sent["body"]["scope_id"] == str(scope)
    calls.clear()
    for extra in ({"actor_id": str(creator)}, {"scope_id": str(uuid4())}, {"limit": True}):
        assert client.post(path, json=body | extra).status_code == 422
    assert client.post(path, json=body, headers={"role": "denied"}).status_code == 403
    assert not calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope_id", str(uuid4())),
        ("publication_id", str(uuid4())),
        ("content_version", "b" * 64),
        ("cursor", "another-page"),
        ("page_sha256", "f" * 64),
    ],
)
def test_wrong_response_scope_version_cursor_or_checksum_fails_closed(field, value):
    client, path, body, state, *_ = setup()
    state["response"][field] = value
    assert client.post(path, json=body).status_code == 503
