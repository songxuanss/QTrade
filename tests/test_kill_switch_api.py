from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade_api.kill_switch import kill_switch_router
from qtrade_application.kill_switch import KillSwitchService
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.kill_switch import KillSwitch
from qtrade_mock.audit import MockAuditStore


class _Security:
    def __init__(self, access):
        self.access = access

    def workspace(self, _permission, *, mutate=False):
        del mutate
        return lambda: self.access


class _Stores:
    def __init__(self):
        self.states = {}
        self.requests = {}

    def get_state(self, workspace_id, account_id=None):
        return self.states.get((workspace_id, account_id))

    def is_active(self, workspace_id, account_id=None):
        return any(
            workspace == workspace_id and active and scope in (None, account_id)
            for (workspace, scope), active in self.states_active().items()
        )

    def states_active(self):
        return {key: value.active for key, value in self.states.items()}

    def set_state(self, state, *, expected_revision):
        key = (state.workspace_id, state.account_id)
        current = self.states.get(key)
        if (current.revision if current else None) != expected_revision:
            raise RuntimeError("revision conflict")
        self.states[key] = state
        return state

    def approve_recovery(self, request, *, approver_id, reason, now):
        current = self.get_state(request.workspace_id, request.account_id)
        result = self.set_state(
            KillSwitch(
                request.workspace_id,
                request.account_id,
                False,
                reason,
                approver_id,
                now,
                current.revision + 1,
            ),
            expected_revision=current.revision,
        )
        self.consume(request.request_id, approver_id=approver_id, now=now)
        return result

    def create(self, request):
        self.requests[request.request_id] = request

    def list_pending(self, workspace_id):
        return tuple(item for item in self.requests.values() if item.workspace_id == workspace_id)

    def get_request(self, request_id):
        return self.requests.get(request_id)

    def consume(self, request_id, *, approver_id, now):
        return self.requests.pop(request_id)


def _client(role=Role.RISK_MANAGER):
    workspace_id, user_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    access = WorkspaceAccess(
        Workspace(workspace_id, "Personal", "personal", None, False, False, now),
        user_id,
        role,
    )
    stores = _Stores()
    service = KillSwitchService(stores, stores, MockAuditStore())
    app = FastAPI()
    app.include_router(kill_switch_router(service, _Security(access)))
    return TestClient(app), workspace_id, user_id, stores


def test_kill_switch_api_activates_account_scope_and_reports_status():
    client, workspace_id, _user_id, _stores = _client()
    account_id = uuid4()
    root = f"/api/v1/workspaces/{workspace_id}/kill-switch"

    assert client.post(
        f"{root}/activate", json={"account_id": str(account_id), "reason": "risk event"}
    ).json() == {"active": True, "revision": 1}
    state = client.get(root, params={"account_id": account_id}).json()
    assert state["active"] is True
    assert state["reason"] == "risk event"
    assert state["revision"] == 1
    assert state["changed_at"]
    assert state["account_id"] == str(account_id)


def test_kill_switch_api_rejects_invalid_activation_payload():
    client, workspace_id, _user_id, _stores = _client()
    root = f"/api/v1/workspaces/{workspace_id}/kill-switch/activate"
    assert client.post(root, json={"reason": ""}).status_code == 422
    assert client.post(root, json={"reason": "x" * 501}).status_code == 422


def test_pending_recovery_is_idempotent_and_marks_the_requester():
    client, workspace_id, _, _ = _client()
    root = f"/api/v1/workspaces/{workspace_id}/kill-switch"
    assert client.post(root + "/activate", json={"reason": "safety stop"}).status_code == 200
    first = client.post(root + "/recovery", json={}).json()
    assert client.post(root + "/recovery", json={}).json() == first
    listed = client.get(root + "/recovery").json()
    assert len(listed) == 1 and listed[0]["request_id"] == first["request_id"]
    assert listed[0]["self_requested"] is True
    assert (
        client.post(
            root + f"/recovery/{first['request_id']}/approve", json={"reason": "self approve"}
        ).status_code
        == 403
    )
    assert client.get(root).json()["active"] is True
    assert len(client.get(root + "/recovery").json()) == 1
