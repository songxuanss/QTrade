"""Durable issuer-scoped replay reservation and session revocation, one transaction."""

from datetime import datetime

from qtrade_domain.audit_port import AuditContext
from qtrade_domain.logout_port import VerifiedLogout
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.audit import append_logout_rows
from qtrade_postgres.connection import PostgresStore


class PostgresLogoutStore(PostgresStore):
    def consume_and_revoke(
        self, event: VerifiedLogout, now: datetime, *, audit_context: AuditContext | None = None
    ) -> int:
        if event.expires_at <= now:
            raise PortError(ErrorCode.INVALID_INPUT, "logout event expired")
        with self._connection() as conn:
            # Expired tokens cannot be verified/consumed; pruning does not permit replay.
            conn.execute("DELETE FROM logout_receipt WHERE expires_at<=%s", (now,))
            receipt = conn.execute(
                """INSERT INTO logout_receipt(issuer,jti,expires_at,consumed_at)
                VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING jti""",
                (event.issuer, event.jti, event.expires_at, now),
            ).fetchone()
            if receipt is None:
                raise PortError(ErrorCode.CONFLICT, "logout event replayed")
            if event.subject is not None:
                # Phase 1 subject logout intentionally terminates all local sessions.
                rows = conn.execute(
                    """UPDATE session s SET revoked_at=%s FROM qtrade_user u
                    WHERE s.user_id=u.user_id AND u.issuer=%s AND u.subject=%s
                    AND s.revoked_at IS NULL RETURNING s.user_id,s.current_workspace_id""",
                    (now, event.issuer, event.subject),
                ).fetchall()
            else:
                rows = conn.execute(
                    """UPDATE session s SET revoked_at=%s FROM qtrade_user u
                    WHERE s.user_id=u.user_id AND u.issuer=%s AND s.sso_sid=%s
                    AND s.revoked_at IS NULL RETURNING s.user_id,s.current_workspace_id""",
                    (now, event.issuer, event.sid),
                ).fetchall()

            append_logout_rows(conn, rows, now, "oidc.backchannel", audit_context=audit_context)
            return len(rows)
