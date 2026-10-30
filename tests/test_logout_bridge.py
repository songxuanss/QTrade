from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade_api.logout_bridge import logout_bridge
from qtrade_application.logout import LogoutService
from qtrade_domain.logout_port import VerifiedLogout
from qtrade_domain.ports import ErrorCode, PortError

SECRET = "test-bff-logout-secret-1234567890123456"


@pytest.fixture
def setup():
    store = Mock()
    store.consume_and_revoke.return_value = 2
    now = datetime.now(UTC)
    service = LogoutService(store, clock=lambda: now)
    app = FastAPI()
    app.include_router(logout_bridge(service, secret=SECRET, issuer="https://sso.test"))
    payload = {
        "issuer": "https://sso.test",
        "jti": "once",
        "expires_at": (now + timedelta(minutes=2)).isoformat(),
        "subject": "one",
        "sid": None,
    }
    return TestClient(app), store, payload, now


@pytest.mark.parametrize("authorization", ["", "Bearer wrong", "Basic wrong"])
def test_untrusted_logout_cannot_revoke(setup, authorization):
    client, store, payload, _ = setup
    response = client.post(
        "/internal/auth/backchannel-logout", json=payload, headers={"Authorization": authorization}
    )
    assert response.status_code == 401
    store.consume_and_revoke.assert_not_called()


def test_valid_notice_reaches_atomic_port_without_browser_cookie(setup):
    client, store, payload, now = setup
    response = client.post(
        "/internal/auth/backchannel-logout",
        json=payload,
        headers={"Authorization": "Bearer " + SECRET},
    )
    assert response.status_code == 204
    assert response.headers["Cache-Control"] == "no-store"
    assert "set-cookie" not in response.headers
    store.consume_and_revoke.assert_called_once()
    assert store.consume_and_revoke.call_args.args == (
        VerifiedLogout(
            "https://sso.test", "once", datetime.fromisoformat(payload["expires_at"]), subject="one"
        ),
        now,
    )
    context = store.consume_and_revoke.call_args.kwargs["audit_context"]
    assert context.source == "api.oidc.backchannel" and context.trace_id
    assert context.source_ip is None


@pytest.mark.parametrize("failure", ["issuer", "expiry", "naive", "jti", "subject", "shape"])
def test_invalid_notice_never_reaches_store(setup, failure):
    client, store, payload, now = setup
    if failure == "issuer":
        payload["issuer"] = "https://other.test"
    if failure == "expiry":
        payload["expires_at"] = now.isoformat()
    if failure == "naive":
        payload["expires_at"] = now.replace(tzinfo=None).isoformat()
    if failure == "jti":
        payload["jti"] = ""
    if failure == "subject":
        payload["subject"] = None
    if failure == "shape":
        payload["logout_token"] = "never-echo-private-token"
    response = client.post(
        "/internal/auth/backchannel-logout",
        json=payload,
        headers={"Authorization": "Bearer " + SECRET},
    )
    assert response.status_code in {400, 401}
    assert "never-echo-private-token" not in response.text
    store.consume_and_revoke.assert_not_called()


@pytest.mark.parametrize("code,status", [(ErrorCode.CONFLICT, 401), (ErrorCode.UNAVAILABLE, 503)])
def test_replay_and_persistence_failure_are_safe(setup, code, status):
    client, store, payload, _ = setup
    store.consume_and_revoke.side_effect = PortError(code, "private-details")
    response = client.post(
        "/internal/auth/backchannel-logout",
        json=payload,
        headers={"Authorization": "Bearer " + SECRET},
    )
    assert response.status_code == status
    assert "private-details" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
