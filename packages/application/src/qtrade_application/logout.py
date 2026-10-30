"""Back-channel logout applies once, independently of browser cookies."""

from collections.abc import Callable
from datetime import UTC, datetime

from qtrade_domain.audit_port import AuditContext
from qtrade_domain.logout_port import LogoutStorePort, VerifiedLogout
from qtrade_domain.ports import ErrorCode, PortError


class LogoutService:
    def __init__(self, store: LogoutStorePort, *, clock: Callable[[], datetime] | None = None):
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def consume(self, event: VerifiedLogout, *, audit_context: AuditContext | None = None) -> int:
        now = self._clock()
        if event.expires_at <= now:
            raise PortError(ErrorCode.INVALID_INPUT, "logout event expired")
        if audit_context is None:
            return self._store.consume_and_revoke(event, now)
        return self._store.consume_and_revoke(event, now, audit_context=audit_context)
