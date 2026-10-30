"""In-memory session store double with the same absolute expiry/revocation semantics."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

from qtrade_domain.audit_port import AuditContext, AuditDetails, AuditRecord, contextualize
from qtrade_domain.identity import Session
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.session_port import EncryptedTokens
from qtrade_mock.audit import MockAuditStore


class MockSessionStore:
    def __init__(self) -> None:
        self.audit = MockAuditStore()
        self.sessions: dict[str, Session] = {}
        self.envelopes: dict[str, EncryptedTokens] = {}
        self.workspace_grants: set[tuple[UUID, UUID]] = set()

    def create(
        self,
        session: Session,
        encrypted: EncryptedTokens,
        *,
        audit_context: AuditContext | None = None,
    ) -> None:
        if session.session_digest in self.sessions:
            raise PortError(ErrorCode.CONFLICT, "session already exists")
        self.audit.append(
            contextualize(
                AuditRecord(
                    session.current_workspace_id,
                    "user",
                    session.user_id,
                    "auth.login",
                    "success",
                    session.created_at,
                    "session",
                    uuid4(),
                    details=AuditDetails(session_digest=session.session_digest),
                ),
                audit_context,
            )
        )
        self.sessions[session.session_digest] = session
        if session.current_workspace_id:
            self.workspace_grants.add((session.user_id, session.current_workspace_id))
        self.envelopes[session.session_digest] = encrypted

    def active(self, digest: str, now: datetime) -> Session | None:
        session = self.sessions.get(digest)
        if session is None or session.revoked_at is not None or session.expires_at <= now:
            return None
        return session

    def switch_workspace(
        self,
        digest: str,
        workspace_id: UUID,
        now: datetime,
        *,
        audit_context: AuditContext | None = None,
    ) -> Session:
        current = self.active(digest, now)
        if current is None or (current.user_id, workspace_id) not in self.workspace_grants:
            raise PortError(ErrorCode.REJECTED, "workspace access unavailable")
        self.audit.append(
            contextualize(
                AuditRecord(
                    workspace_id,
                    "user",
                    current.user_id,
                    "auth.workspace.switch",
                    "success",
                    now,
                    "session",
                    uuid4(),
                    details=AuditDetails(session_digest=digest, target_workspace_id=workspace_id),
                ),
                audit_context,
            )
        )
        result = replace(current, current_workspace_id=workspace_id)
        self.sessions[digest] = result
        return result

    def encrypted_tokens(self, digest: str, now: datetime) -> EncryptedTokens | None:
        return self.envelopes.get(digest) if self.active(digest, now) is not None else None

    def revoke(
        self, digest: str, now: datetime, *, audit_context: AuditContext | None = None
    ) -> None:
        session = self.sessions.get(digest)
        if session is not None and session.revoked_at is None:
            self.audit.append(
                contextualize(
                    AuditRecord(
                        session.current_workspace_id,
                        "user",
                        session.user_id,
                        "auth.logout",
                        "success",
                        now,
                        "session",
                        uuid4(),
                        details=AuditDetails(session_digest=digest),
                    ),
                    audit_context,
                )
            )
            self.sessions[digest] = replace(session, revoked_at=now)

    def revoke_user(
        self, user_id: UUID, now: datetime, *, audit_context: AuditContext | None = None
    ) -> int:
        count = 0
        for digest, session in tuple(self.sessions.items()):
            if session.user_id == user_id and session.revoked_at is None:
                self.revoke(digest, now, audit_context=audit_context)
                count += 1
        return count
