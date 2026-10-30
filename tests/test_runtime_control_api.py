from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade_api.runtime_controls import runtime_control_router
from qtrade_application.runtime_control import RuntimeControlResult
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.runtime_control import ExecutionRuntime


class _Security:
    def __init__(self, access):
        self.access = access

    def workspace(self, _permission, *, mutate=False):
        return lambda: self.access


class _Service:
    def list(self, access):
        return ()

    def control(self, access, runtime_id, action, *, confirmation, now, audit_context):
        self.args = (runtime_id, action, confirmation)
        runtime = ExecutionRuntime(
            access.workspace.workspace_id,
            runtime_id,
            uuid4(),
            uuid4(),
            "paper",
            "paused",
            2,
            now,
        )
        return RuntimeControlResult(action, runtime, 3)


def test_runtime_control_endpoint_posts_confirmation_and_returns_state():
    now = datetime.now(UTC)
    access = WorkspaceAccess(
        Workspace(uuid4(), "Personal", "personal", None, False, False, now),
        uuid4(),
        Role.ANALYST,
    )
    service = _Service()
    app = FastAPI()
    app.include_router(runtime_control_router(service, _Security(access)))

    response = TestClient(app).post(
        f"/api/v1/workspaces/{access.workspace.workspace_id}/runtimes/{uuid4()}/control",
        json={"action": "stop", "confirmation": "CONFIRM STOP"},
    )

    assert response.status_code == 200
    assert response.json()["runtime"]["state"] == "paused"
    assert response.json()["affected_count"] == 3
    assert service.args[1:] == ("stop", "CONFIRM STOP")


def test_runtime_control_api_validates_actions_and_confirmation_shape():
    now = datetime.now(UTC)
    access = WorkspaceAccess(
        Workspace(uuid4(), "Personal", "personal", None, False, False, now),
        uuid4(),
        Role.ANALYST,
    )
    app = FastAPI()
    app.include_router(runtime_control_router(_Service(), _Security(access)))
    root = f"/api/v1/workspaces/{access.workspace.workspace_id}/runtimes/{uuid4()}/control"

    assert TestClient(app).post(root, json={"action": "unknown"}).status_code == 422
    assert (
        TestClient(app).post(root, json={"action": "stop", "confirmation": "x" * 65}).status_code
        == 422
    )
