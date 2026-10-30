"""Sessions and encryption are replaceable persistence/infrastructure concerns."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrade_domain.audit_port import AuditContext
from qtrade_domain.identity import Session


@dataclass(frozen=True)
class EncryptedTokens:
    key_id: str
    ciphertext: bytes = field(repr=False)


class TokenCipherPort(Protocol):
    def encrypt(self, plaintext: bytes, *, context: bytes) -> EncryptedTokens: ...
    def decrypt(self, encrypted: EncryptedTokens, *, context: bytes) -> bytes: ...


class SessionStorePort(Protocol):
    def create(
        self,
        session: Session,
        encrypted: EncryptedTokens,
        *,
        audit_context: AuditContext | None = None,
    ) -> None: ...
    def active(self, digest: str, now: datetime) -> Session | None: ...
    def switch_workspace(
        self,
        digest: str,
        workspace_id: UUID,
        now: datetime,
        *,
        audit_context: AuditContext | None = None,
    ) -> Session: ...
    def encrypted_tokens(self, digest: str, now: datetime) -> EncryptedTokens | None: ...
    def revoke(
        self, digest: str, now: datetime, *, audit_context: AuditContext | None = None
    ) -> None: ...
    def revoke_user(
        self, user_id: UUID, now: datetime, *, audit_context: AuditContext | None = None
    ) -> int: ...
