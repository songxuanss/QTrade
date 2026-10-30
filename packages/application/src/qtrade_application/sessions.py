"""Opaque, absolute-lifetime sessions. Token retrieval is server-only."""

import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from uuid import UUID

from qtrade_domain.audit_port import AuditContext
from qtrade_domain.identity import Session
from qtrade_domain.identity_port import LoginIdentity
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.session_port import SessionStorePort, TokenCipherPort


@dataclass(frozen=True)
class TokenSet:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    id_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CreatedSession:
    cookie: str = field(repr=False)
    csrf: str = field(repr=False)
    session: Session


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class SessionService:
    def __init__(
        self,
        store: SessionStorePort,
        cipher: TokenCipherPort,
        *,
        lifetime: timedelta = timedelta(hours=8),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if not timedelta(minutes=1) <= lifetime <= timedelta(hours=12):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid session lifetime")
        self._store, self._cipher = store, cipher
        self._lifetime, self._clock = lifetime, clock

    def create(
        self,
        identity: LoginIdentity,
        tokens: TokenSet,
        *,
        sso_sid: str | None = None,
        audit_context: AuditContext | None = None,
    ) -> CreatedSession:
        member = identity.membership
        if (
            identity.user.disabled
            or identity.personal_workspace.archived
            or member is None
            or not member.active
            or member.user_id != identity.user.user_id
            or member.workspace_id != identity.personal_workspace.workspace_id
        ):
            raise PortError(ErrorCode.UNAUTHORIZED, "workspace access unavailable")
        if (
            not isinstance(tokens.access_token, str)
            or not tokens.access_token
            or any(
                value is not None and (not isinstance(value, str) or len(value) > 65536)
                for value in (tokens.access_token, tokens.refresh_token, tokens.id_token)
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "access token is required")
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = self._clock()
        session = Session(
            digest(cookie),
            identity.user.user_id,
            identity.personal_workspace.workspace_id,
            sso_sid,
            digest(csrf),
            now,
            now,
            now + self._lifetime,
            None,
        )
        payload = json.dumps(
            {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "id_token": tokens.id_token,
                "csrf": csrf,
            },
            separators=(",", ":"),
        ).encode()
        encrypted = self._cipher.encrypt(payload, context=session.session_digest.encode())
        self._store.create(session, encrypted, audit_context=audit_context)
        return CreatedSession(cookie, csrf, session)

    def authenticate(self, cookie: str | None) -> Session:
        if cookie is None or not re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie):
            raise PortError(ErrorCode.UNAUTHORIZED, "authentication required")
        session = self._store.active(digest(cookie), self._clock())
        if session is None:
            raise PortError(ErrorCode.UNAUTHORIZED, "authentication required")
        return session

    def _payload(self, cookie: str) -> dict[str, str | None]:
        session = self.authenticate(cookie)
        encrypted = self._store.encrypted_tokens(session.session_digest, self._clock())
        if encrypted is None:
            raise PortError(ErrorCode.UNAUTHORIZED, "authentication required")
        try:
            payload = json.loads(
                self._cipher.decrypt(encrypted, context=session.session_digest.encode())
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
                raise ValueError("invalid token payload")
            if (
                not isinstance(payload.get("csrf"), str)
                or digest(payload["csrf"]) != session.csrf_digest
            ):
                raise ValueError("invalid CSRF payload")
            if any(value is not None and not isinstance(value, str) for value in payload.values()):
                raise ValueError("invalid token values")
            return {
                key: payload.get(key)
                for key in ("access_token", "refresh_token", "id_token", "csrf")
            }
        except (ValueError, TypeError):
            raise PortError(ErrorCode.UNAUTHORIZED, "token envelope invalid") from None

    def tokens(self, cookie: str) -> TokenSet:
        payload = self._payload(cookie)
        access = payload["access_token"]
        if access is None:
            raise PortError(ErrorCode.UNAUTHORIZED, "token envelope invalid")
        return TokenSet(access, payload["refresh_token"], payload["id_token"])

    def csrf(self, cookie: str) -> str:
        token = self._payload(cookie)["csrf"]
        if token is None:
            raise PortError(ErrorCode.UNAUTHORIZED, "token envelope invalid")
        return token

    def switch_workspace(
        self,
        cookie: str,
        workspace_id: UUID,
        *,
        audit_context: AuditContext | None = None,
    ) -> Session:
        self.authenticate(cookie)
        return self._store.switch_workspace(
            digest(cookie), workspace_id, self._clock(), audit_context=audit_context
        )

    def verify_csrf(self, cookie: str, token: str | None) -> None:
        session = self.authenticate(cookie)
        if (
            token is None
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
            or not compare_digest(digest(token), session.csrf_digest)
        ):
            raise PortError(ErrorCode.UNAUTHORIZED, "CSRF verification failed")

    def revoke(self, cookie: str | None, *, audit_context: AuditContext | None = None) -> None:
        if cookie is not None and re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie):
            self._store.revoke(digest(cookie), self._clock(), audit_context=audit_context)
