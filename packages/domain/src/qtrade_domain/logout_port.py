"""Verified provider logout events and atomic revocation boundary."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from qtrade_domain.audit_port import AuditContext


@dataclass(frozen=True)
class VerifiedLogout:
    issuer: str
    jti: str
    expires_at: datetime
    subject: str | None = None
    sid: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.issuer
            or not self.jti
            or len(self.jti) > 256
            or self.expires_at.tzinfo is None
            or not (self.subject or self.sid)
            or any(v is not None and (not v or len(v) > 512) for v in (self.subject, self.sid))
        ):
            raise ValueError("invalid verified logout")


class LogoutStorePort(Protocol):
    def consume_and_revoke(
        self, event: VerifiedLogout, now: datetime, *, audit_context: AuditContext | None = None
    ) -> int:
        """Atomically reserve issuer/jti and revoke matches; replay raises CONFLICT."""
        ...
