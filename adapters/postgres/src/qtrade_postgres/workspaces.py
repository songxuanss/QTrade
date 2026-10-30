"""Live membership queries; workspace row locks serialize member mutations."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import class_row

from qtrade_domain.audit_port import AuditContext, AuditDetails, AuditRecord, contextualize
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.identity import Membership, Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.audit import append_in_transaction
from qtrade_postgres.connection import PostgresStore


class PostgresWorkspaceStore(PostgresStore):
    @staticmethod
    def _access(
        conn: psycopg.Connection[tuple[object, ...]], actor: UUID, workspace: UUID
    ) -> WorkspaceAccess | None:
        row = conn.execute(
            """SELECT m.role FROM membership m JOIN qtrade_user u ON u.user_id=m.user_id
            JOIN workspace w ON w.workspace_id=m.workspace_id
            WHERE m.user_id=%s AND m.workspace_id=%s AND m.active AND NOT u.disabled
            AND NOT w.archived""",
            (actor, workspace),
        ).fetchone()
        if row is None:
            return None
        with conn.cursor(row_factory=class_row(Workspace)) as cursor:
            cursor.execute("SELECT * FROM workspace WHERE workspace_id=%s", (workspace,))
            found = cursor.fetchone()
        return WorkspaceAccess(found, actor, Role(str(row[0]))) if found else None

    @staticmethod
    def _require_admin(
        conn: psycopg.Connection[tuple[object, ...]], actor: UUID, workspace: UUID
    ) -> None:
        conn.execute(
            "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE", (workspace,)
        )
        access = PostgresWorkspaceStore._access(conn, actor, workspace)
        if access is None:
            raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
        access.require(Permission.MEMBER_MANAGE)

    def accessible(self, user_id: UUID) -> tuple[WorkspaceAccess, ...]:
        with self._connection() as conn:
            ids = conn.execute(
                """SELECT m.workspace_id FROM membership m JOIN workspace w USING(workspace_id)
                JOIN qtrade_user u USING(user_id) WHERE m.user_id=%s AND m.active
                AND NOT w.archived AND NOT u.disabled ORDER BY w.created_at,w.workspace_id""",
                (user_id,),
            ).fetchall()
            return tuple(
                access
                for row in ids
                if (access := self._access(conn, user_id, UUID(str(row[0])))) is not None
            )

    def access(self, user_id: UUID, workspace_id: UUID) -> WorkspaceAccess | None:
        with self._connection() as conn:
            return self._access(conn, user_id, workspace_id)

    @staticmethod
    def _member(
        conn: psycopg.Connection[tuple[object, ...]], workspace: UUID, user: UUID
    ) -> Membership:
        with conn.cursor(row_factory=class_row(Membership)) as cursor:
            cursor.execute(
                "SELECT * FROM membership WHERE workspace_id=%s AND user_id=%s", (workspace, user)
            )
            result = cursor.fetchone()
        if result is None:
            raise PortError(ErrorCode.NOT_FOUND, "membership unavailable")
        return Membership(
            result.workspace_id,
            result.user_id,
            Role(result.role),
            result.active,
            result.created_by,
            result.created_at,
            result.updated_at,
        )

    def members(self, actor_id: UUID, workspace_id: UUID) -> tuple[Membership, ...]:
        with self._connection() as conn:
            self._require_admin(conn, actor_id, workspace_id)
            users = conn.execute(
                "SELECT user_id FROM membership WHERE workspace_id=%s ORDER BY user_id",
                (workspace_id,),
            ).fetchall()
            return tuple(self._member(conn, workspace_id, UUID(str(row[0]))) for row in users)

    def set_member(
        self,
        actor_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        *,
        role: Role,
        active: bool,
        audit_context: AuditContext | None = None,
    ) -> Membership:
        with self._connection() as conn:
            self._require_admin(conn, actor_id, workspace_id)
            target = conn.execute(
                "SELECT disabled FROM qtrade_user WHERE user_id=%s FOR SHARE", (user_id,)
            ).fetchone()
            if target is None or (active and target[0]):
                raise PortError(ErrorCode.NOT_FOUND, "membership target unavailable")
            current = conn.execute(
                "SELECT role,active FROM membership WHERE workspace_id=%s AND user_id=%s",
                (workspace_id, user_id),
            ).fetchone()
            if current and current == (Role.ADMIN, True) and (not active or role != Role.ADMIN):
                count = conn.execute(
                    """SELECT count(*) FROM membership m JOIN qtrade_user u USING(user_id)
                                      WHERE m.workspace_id=%s AND m.active AND m.role='admin'
                                      AND NOT u.disabled""",
                    (workspace_id,),
                ).fetchone()
                if count is None or int(str(count[0])) <= 1:
                    raise PortError(
                        ErrorCode.CONFLICT, "workspace requires an active administrator"
                    )
            conn.execute(
                """INSERT INTO membership(workspace_id,user_id,role,active,created_by)
                            VALUES (%s,%s,%s,%s,%s) ON CONFLICT(workspace_id,user_id)
                            DO UPDATE SET role=excluded.role,active=excluded.active,
                            updated_at=now()""",
                (workspace_id, user_id, role, active, actor_id),
            )
            append_in_transaction(
                conn,
                contextualize(
                    AuditRecord(
                        workspace_id,
                        "user",
                        actor_id,
                        "membership.change",
                        "success",
                        datetime.now(UTC),
                        "workspace",
                        uuid4(),
                        details=AuditDetails(
                            target_id=user_id,
                            role_before=Role(str(current[0])) if current else None,
                            active_before=bool(current[1]) if current else None,
                            role_after=role,
                            active_after=active,
                        ),
                    ),
                    audit_context,
                ),
            )
            return self._member(conn, workspace_id, user_id)
