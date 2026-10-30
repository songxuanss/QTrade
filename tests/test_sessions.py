from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import Response

from qtrade_api.sessions import session_router
from qtrade_application.sessions import SessionService, TokenSet, digest
from qtrade_crypto.tokens import TokenCipher
from qtrade_domain.identity import Membership, Role, User, Workspace
from qtrade_domain.identity_port import LoginIdentity
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.session_port import EncryptedTokens
from qtrade_mock.sessions import MockSessionStore
from qtrade_runtime.cookies import SessionCookie

NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def identity():
    user, workspace = uuid4(), uuid4()
    return LoginIdentity(
        User(user, "https://sso.test", "subject", "Name", None, False, NOW, NOW),
        Workspace(workspace, "Personal", "personal", user, False, False, NOW),
        Membership(workspace, user, Role.ADMIN, True, user, NOW, NOW),
    )


@pytest.fixture
def setup(identity):
    store = MockSessionStore()
    cipher = TokenCipher({"v1": b"a" * 32}, primary="v1")
    clock = [NOW]
    service = SessionService(store, cipher, clock=lambda: clock[0])
    tokens = TokenSet("private-access-token", "private-refresh-token", "private-id-token")
    created = service.create(identity, tokens, sso_sid="sso-session")
    return service, store, clock, created, tokens, cipher


def test_opaque_cookie_encrypted_storage_and_server_only_tokens(setup):
    service, store, _, created, tokens, _ = setup
    assert len(created.cookie) == 43
    assert created.cookie not in store.sessions
    assert created.session.session_digest == digest(created.cookie)
    assert created.csrf not in repr(created)
    assert tokens.access_token not in repr(tokens)
    encrypted = store.envelopes[created.session.session_digest]
    for value in (tokens.access_token, tokens.refresh_token, tokens.id_token, created.csrf):
        assert value.encode() not in encrypted.ciphertext
    assert service.tokens(created.cookie) == tokens
    assert service.csrf(created.cookie) == created.csrf
    assert service.authenticate(created.cookie).sso_sid == "sso-session"
    service.verify_csrf(created.cookie, created.csrf)


def test_absolute_expiry_and_revocation_immediately_deny_tokens(setup):
    service, store, clock, created, _, _ = setup
    clock[0] = created.session.expires_at - timedelta(microseconds=1)
    service.authenticate(created.cookie)
    clock[0] = created.session.expires_at
    for call in (service.authenticate, service.tokens, service.csrf):
        with pytest.raises(PortError):
            call(created.cookie)
    clock[0] = NOW
    service.revoke(created.cookie)
    service.revoke(created.cookie)
    assert store.sessions[created.session.session_digest].revoked_at == NOW
    with pytest.raises(PortError):
        service.tokens(created.cookie)


@pytest.mark.parametrize("cookie", [None, "", "forged", "x" * 43, "../" + "x" * 40])
def test_invalid_and_unknown_cookies_deny_access(setup, cookie):
    with pytest.raises(PortError) as caught:
        setup[0].authenticate(cookie)
    assert caught.value.code == ErrorCode.UNAUTHORIZED


@pytest.mark.parametrize("csrf", [None, "", "forged", "x" * 43])
def test_csrf_is_bound_to_session(setup, csrf):
    with pytest.raises(PortError):
        setup[0].verify_csrf(setup[3].cookie, csrf)


def test_removed_workspace_membership_or_disabled_identity_cannot_create(identity):
    service = SessionService(MockSessionStore(), TokenCipher({"v1": b"a" * 32}, primary="v1"))
    for denied in (
        replace(identity, membership=None),
        replace(identity, membership=replace(identity.membership, active=False)),
        replace(identity, user=replace(identity.user, disabled=True)),
        replace(identity, personal_workspace=replace(identity.personal_workspace, archived=True)),
    ):
        with pytest.raises(PortError):
            service.create(denied, TokenSet("access"))


def test_cipher_random_nonce_tamper_and_session_binding(setup):
    cipher = setup[5]
    first = cipher.encrypt(b"secret", context=b"session-one")
    second = cipher.encrypt(b"secret", context=b"session-one")
    assert first.ciphertext != second.ciphertext
    assert cipher.decrypt(first, context=b"session-one") == b"secret"
    for invalid, context in (
        (first, b"session-two"),
        (
            replace(first, ciphertext=first.ciphertext[:-1] + bytes([first.ciphertext[-1] ^ 1])),
            b"session-one",
        ),
        (EncryptedTokens("v1", b"short"), b"session-one"),
        (replace(first, key_id="unknown"), b"session-one"),
    ):
        with pytest.raises(PortError):
            cipher.decrypt(invalid, context=context)


def test_key_rotation_reads_old_sessions_without_reencrypting(setup):
    service, store, clock, created, tokens, _ = setup
    rotated = SessionService(
        store, TokenCipher({"v1": b"a" * 32, "v2": b"b" * 32}, primary="v2"), clock=lambda: clock[0]
    )
    assert rotated.tokens(created.cookie) == tokens
    retired = SessionService(
        store, TokenCipher({"v2": b"b" * 32}, primary="v2"), clock=lambda: clock[0]
    )
    with pytest.raises(PortError):
        retired.tokens(created.cookie)


@pytest.mark.parametrize("environment", ["production", "staging", "local", "test"])
def test_cookie_defaults_secure_httponly_host_only(environment):
    policy = SessionCookie(environment)
    response = Response()
    policy.set(response, "opaque", max_age=60)
    cookie = response.headers["set-cookie"]
    assert "__Host-qtrade-session=opaque" in cookie
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=60"):
        assert flag in cookie
    assert "Domain=" not in cookie
    assert response.headers["cache-control"] == "no-store"
    response = Response()
    policy.clear(response)
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_insecure_cookie_configuration_rejected(environment):
    with pytest.raises(ValueError):
        SessionCookie(environment, insecure_local=True)


def test_http_session_response_never_returns_tokens_and_logout_revokes(setup):
    service, _, _, created, tokens, _ = setup
    app = FastAPI()
    policy = SessionCookie("test", insecure_local=True)
    app.include_router(session_router(service, policy))
    client = TestClient(app)
    anonymous = client.get("/auth/session")
    assert anonymous.status_code == 401
    assert anonymous.headers["cache-control"] == "no-store"
    client.cookies.set(policy.name, created.cookie)
    response = client.get("/auth/session")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["csrf_token"] == created.csrf
    for secret in (tokens.access_token, tokens.refresh_token, tokens.id_token, created.cookie):
        assert secret not in response.text
    assert client.post("/auth/logout").status_code == 403
    assert client.get("/auth/session").status_code == 200
    response = client.post("/auth/logout", headers={"X-CSRF-Token": created.csrf})
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/auth/session").status_code == 401


def test_session_composition_has_no_default_keys_and_production_fails_closed():
    from qtrade_api.session_config import configure_sessions

    service, policy = configure_sessions({"QTRADE_ENV": "local"})
    assert service is None
    assert policy.insecure_local is False
    for mode in ("production", "staging"):
        with pytest.raises(ValueError):
            configure_sessions({"QTRADE_ENV": mode})


def test_session_composition_uses_explicit_keyring_and_rejects_unsafe_config():
    import base64
    import json

    from qtrade_api.session_config import configure_sessions

    settings = {
        "QTRADE_ENV": "production",
        "QTRADE_SESSION_PRIMARY_KEY": "v1",
        "QTRADE_SESSION_KEYS_JSON": json.dumps({"v1": base64.b64encode(b"a" * 32).decode()}),
        "DATABASE_URL": "postgresql://unconnected/test",
    }
    service, policy = configure_sessions(settings)
    assert service is not None
    assert policy.name == "__Host-qtrade-session"
    for changed in (
        {"QTRADE_INSECURE_LOCAL_COOKIES": "true"},
        {"QTRADE_SESSION_KEYS_JSON": "invalid"},
        {"QTRADE_SESSION_KEYS_JSON": '{"v1":"bad base64"}'},
        {"QTRADE_SESSION_PRIMARY_KEY": "unknown"},
        {"QTRADE_SESSION_LIFETIME_SECONDS": "0"},
    ):
        with pytest.raises((ValueError, PortError)):
            configure_sessions({**settings, **changed})


def test_workspace_selection_is_authorized_and_preserves_token_envelope_and_absolute_expiry(setup):
    service, store, clock, created, tokens, _ = setup
    target = uuid4()
    with pytest.raises(PortError, match="workspace access unavailable"):
        service.switch_workspace(created.cookie, target)
    assert (
        service.authenticate(created.cookie).current_workspace_id
        == created.session.current_workspace_id
    )
    store.workspace_grants.add((created.session.user_id, target))
    selected = service.switch_workspace(created.cookie, target)
    assert selected.current_workspace_id == target
    assert selected.expires_at == created.session.expires_at
    assert selected.created_at == created.session.created_at
    assert service.tokens(created.cookie) == tokens
    assert service.csrf(created.cookie) == created.csrf
    assert store.audit.records[-1].action == "auth.workspace.switch"
    clock[0] = selected.expires_at
    with pytest.raises(PortError, match="authentication required"):
        service.switch_workspace(created.cookie, created.session.current_workspace_id)


def test_workspace_selection_http_requires_csrf_and_an_actual_grant(setup):
    service, store, _, created, _, _ = setup
    app = FastAPI()
    app.include_router(
        session_router(service, SessionCookie(environment="test", insecure_local=True))
    )
    client = TestClient(app)
    client.cookies.set("qtrade-session", created.cookie)
    target = uuid4()
    store.workspace_grants.add((created.session.user_id, target))
    assert (
        client.post("/auth/session/workspace", json={"workspace_id": str(target)}).status_code
        == 403
    )
    assert (
        client.post(
            "/auth/session/workspace",
            headers={"X-CSRF-Token": created.csrf},
            json={"workspace_id": str(uuid4())},
        ).status_code
        == 403
    )
    response = client.post(
        "/auth/session/workspace",
        headers={"X-CSRF-Token": created.csrf},
        json={"workspace_id": str(target)},
    )
    assert response.status_code == 200 and response.json()["workspace_id"] == str(target)
    assert client.get("/auth/session").json()["workspace_id"] == str(target)
