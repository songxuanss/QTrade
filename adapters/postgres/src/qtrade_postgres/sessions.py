"""Session authorization is rechecked against live user/workspace/membership rows."""

from datetime import datetime
from uuid import UUID, uuid4

from psycopg.rows import class_row

from qtrade_domain.audit_port import AuditContext, AuditDetails, AuditRecord, contextualize
from qtrade_domain.identity import Session
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.session_port import EncryptedTokens
from qtrade_postgres.audit import append_in_transaction, append_logout_rows
from qtrade_postgres.connection import PostgresStore

ACTIVE = """FROM session s
    JOIN qtrade_user u ON u.user_id=s.user_id
    JOIN workspace w ON w.workspace_id=s.current_workspace_id
    JOIN membership m ON m.workspace_id=w.workspace_id AND m.user_id=u.user_id
    WHERE s.session_digest=%s AND s.expires_at>%s AND s.revoked_at IS NULL
      AND NOT u.disabled AND NOT w.archived AND m.active"""


class PostgresSessionStore(PostgresStore):
    def create(
        self,
        session: Session,
        encrypted: EncryptedTokens,
        *,
        audit_context: AuditContext | None = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO session(session_digest,user_id,current_workspace_id,sso_sid,
                csrf_digest,
                created_at,last_seen_at,expires_at,revoked_at,token_ciphertext,encryption_key_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    session.session_digest,
                    session.user_id,
                    session.current_workspace_id,
                    session.sso_sid,
                    session.csrf_digest,
                    session.created_at,
                    session.last_seen_at,
                    session.expires_at,
                    session.revoked_at,
                    encrypted.ciphertext,
                    encrypted.key_id,
                ),
            )

            append_in_transaction(
                conn,
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
                ),
            )

    def active(self, digest: str, now: datetime) -> Session | None:
        with self._connection() as conn, conn.cursor(row_factory=class_row(Session)) as cursor:
            cursor.execute(
                """SELECT s.session_digest,s.user_id,s.current_workspace_id,s.sso_sid,s.csrf_digest,
                s.created_at,s.last_seen_at,s.expires_at,s.revoked_at """
                + ACTIVE,
                (digest, now),
            )
            return cursor.fetchone()

    def switch_workspace(
        self,
        digest: str,
        workspace_id: UUID,
        now: datetime,
        *,
        audit_context: AuditContext | None = None,
    ) -> Session:
        with self._connection() as conn:
            with conn.cursor(row_factory=class_row(Session)) as cursor:
                cursor.execute(
                    """UPDATE session s SET current_workspace_id=%s
                    FROM membership m JOIN workspace w ON w.workspace_id=m.workspace_id
                    JOIN qtrade_user u ON u.user_id=m.user_id
                    WHERE s.session_digest=%s AND s.user_id=m.user_id AND m.workspace_id=%s
                    AND m.active AND NOT w.archived AND NOT u.disabled
                    AND s.expires_at>%s AND s.revoked_at IS NULL
                    RETURNING s.session_digest,s.user_id,s.current_workspace_id,s.sso_sid,
                    s.csrf_digest,s.created_at,s.last_seen_at,s.expires_at,s.revoked_at""",
                    (workspace_id, digest, workspace_id, now),
                )
                result = cursor.fetchone()
            if result is None:
                raise PortError(ErrorCode.REJECTED, "workspace access unavailable")
            append_in_transaction(
                conn,
                contextualize(
                    AuditRecord(
                        workspace_id,
                        "user",
                        result.user_id,
                        "auth.workspace.switch",
                        "success",
                        now,
                        "session",
                        uuid4(),
                        details=AuditDetails(
                            session_digest=digest, target_workspace_id=workspace_id
                        ),
                    ),
                    audit_context,
                ),
            )
            return result

    def encrypted_tokens(self, digest: str, now: datetime) -> EncryptedTokens | None:
        with (
            self._connection() as conn,
            conn.cursor(row_factory=class_row(EncryptedTokens)) as cursor,
        ):
            cursor.execute(
                "SELECT s.encryption_key_id AS key_id,s.token_ciphertext AS ciphertext " + ACTIVE,
                (digest, now),
            )
            return cursor.fetchone()

    def revoke(
        self, digest: str, now: datetime, *, audit_context: AuditContext | None = None
    ) -> None:
        with self._connection() as conn:
            row = conn.execute(
                "UPDATE session SET revoked_at=%s WHERE session_digest=%s AND revoked_at IS NULL "
                "RETURNING user_id,current_workspace_id",
                (now, digest),
            ).fetchone()
            if row is not None:
                append_in_transaction(
                    conn,
                    contextualize(
                        AuditRecord(
                            UUID(str(row[1])) if row[1] is not None else None,
                            "user",
                            UUID(str(row[0])),
                            "auth.logout",
                            "success",
                            now,
                            "session",
                            uuid4(),
                            details=AuditDetails(session_digest=digest),
                        ),
                        audit_context,
                    ),
                )

    def revoke_user(
        self, user_id: UUID, now: datetime, *, audit_context: AuditContext | None = None
    ) -> int:
        with self._connection() as conn:
            rows = conn.execute(
                "UPDATE session SET revoked_at=%s WHERE user_id=%s AND revoked_at IS NULL "
                "RETURNING user_id,current_workspace_id",
                (now, user_id),
            ).fetchall()
            append_logout_rows(conn, rows, now, "session", audit_context=audit_context)
            return len(rows)
