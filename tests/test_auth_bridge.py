from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade_api.auth_bridge import auth_bridge
from qtrade_application.sessions import SessionService, TokenSet
from qtrade_crypto.tokens import TokenCipher
from qtrade_domain.identity import Membership, Role, User, Workspace
from qtrade_domain.identity_port import LoginIdentity
from qtrade_mock.audit import MockAuditStore
from qtrade_mock.sessions import MockSessionStore
from qtrade_runtime.cookies import SessionCookie

SECRET = "test-bff-secret-not-production-1234567890"


@pytest.fixture
def setup():
    now = datetime.now(UTC)
    user, workspace = uuid4(), uuid4()
    mapped = LoginIdentity(
        User(user, "https://sso.test", "subject", "Name", None, False, now, now),
        Workspace(workspace, "Personal", "personal", user, False, False, now),
        Membership(workspace, user, Role.ADMIN, True, user, now, now),
    )
    identity = Mock()
    identity.initialize.return_value = mapped
    sessions = SessionService(MockSessionStore(), TokenCipher({"test": b"a" * 32}, primary="test"))
    app = FastAPI()
    policy = SessionCookie("test", insecure_local=True)
    app.include_router(
        auth_bridge(identity, sessions, policy, secret=SECRET, issuer="https://sso.test")
    )
    payload = {
        "identity": {
            "issuer": "https://sso.test",
            "subject": "subject",
            "display_name": "Name",
            "email": None,
        },
        "tokens": {
            "access_token": "private-access",
            "refresh_token": "private-refresh",
            "id_token": "private-id",
        },
        "sso_sid": None,
    }
    return TestClient(app), identity, sessions, payload, policy


def test_failed_login_is_anonymous_and_never_accepts_claims_or_tokens():
    audit = MockAuditStore()
    app = FastAPI()
    app.include_router(
        auth_bridge(
            None,
            None,
            SessionCookie("test", insecure_local=True),
            secret=SECRET,
            issuer="https://sso.test",
            audit=audit,
        )
    )
    client = TestClient(app)
    path = "/internal/auth/login-failed"
    assert client.post(path).status_code == 401
    headers = {"Authorization": "Bearer " + SECRET}
    assert client.post(path, headers=headers, json={"token": "private"}).status_code == 400
    assert audit.records == []
    response = client.post(path, headers=headers)
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    (record,) = audit.records
    assert record.actor_type == "anonymous" and record.actor_id is None
    assert record.workspace_id is None and record.action == "auth.login"
    assert record.result == "failure" and record.source == "api.oidc.callback"
    assert record.details.reason.value == "unauthorized"
    assert "private" not in repr(record)


def test_failed_login_audit_outage_returns_unavailable_without_session():
    from qtrade_domain.ports import ErrorCode, PortError

    audit = Mock()
    audit.append.side_effect = PortError(ErrorCode.UNAVAILABLE, "private database details")
    app = FastAPI()
    app.include_router(
        auth_bridge(
            None,
            None,
            SessionCookie("test", insecure_local=True),
            secret=SECRET,
            issuer="https://sso.test",
            audit=audit,
        )
    )
    response = TestClient(app).post(
        "/internal/auth/login-failed", headers={"Authorization": "Bearer " + SECRET}
    )
    assert response.status_code == 503
    assert "private" not in response.text and "set-cookie" not in response.headers


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic irrelevant"])
def test_browser_cannot_create_session_or_map_identity(setup, authorization):
    client, identity, _, payload, _ = setup
    response = client.post(
        "/internal/auth/session",
        json=payload,
        headers={"Authorization": authorization} if authorization else {},
    )
    assert response.status_code == 401
    identity.initialize.assert_not_called()
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"


def test_trusted_verified_identity_creates_only_opaque_httponly_cookie(setup):
    client, identity, sessions, payload, policy = setup
    response = client.post(
        "/internal/auth/session", json=payload, headers={"Authorization": "Bearer " + SECRET}
    )
    assert response.status_code == 204
    identity.initialize.assert_called_once()
    cookie = client.cookies.get(policy.name)
    assert sessions.tokens(cookie) == TokenSet(**payload["tokens"])
    assert "HttpOnly" in response.headers["set-cookie"]
    assert not response.content
    for secret in payload["tokens"].values():
        assert secret not in response.headers["set-cookie"]


@pytest.mark.parametrize("change", ["issuer", "extra", "bad_token", "missing_id", "long_name"])
def test_invalid_identity_payload_never_maps_or_echoes_tokens(setup, change):
    client, identity, _, payload, _ = setup
    if change == "issuer":
        payload["identity"]["issuer"] = "https://wrong.test"
    if change == "extra":
        payload["administrator"] = True
    if change == "bad_token":
        payload["tokens"]["access_token"] = {"secret": "private-access"}
    if change == "missing_id":
        payload["tokens"]["id_token"] = None
    if change == "long_name":
        payload["identity"]["display_name"] = "x" * 257
    response = client.post(
        "/internal/auth/session", json=payload, headers={"Authorization": "Bearer " + SECRET}
    )
    assert response.status_code in {400, 403}
    identity.initialize.assert_not_called()
    assert "private-access" not in response.text
    assert "set-cookie" not in response.headers
