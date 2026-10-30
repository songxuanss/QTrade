"""Atomic identity mapping. PostgreSQL unique constraints serialize concurrent logins."""

import re
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import class_row, dict_row

from qtrade_domain.identity import Membership, Role, User, Workspace
from qtrade_domain.identity_port import LoginIdentity, VerifiedIdentity
from qtrade_domain.ports import ErrorCode, PortError


class PostgresIdentityStore:
    def __init__(self, database_url: str, *, schema: str = "qtrade"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid database schema")
        self._url = database_url
        self._schema = schema

    def initialize(self, identity: VerifiedIdentity, *, workspace_name: str) -> LoginIdentity:
        try:
            return self._initialize(identity, workspace_name=workspace_name)
        except psycopg.Error:
            raise PortError(ErrorCode.UNAVAILABLE, "identity persistence unavailable") from None

    def _initialize(self, identity: VerifiedIdentity, *, workspace_name: str) -> LoginIdentity:
        if not workspace_name or len(workspace_name) > 256:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid personal workspace name")
        with psycopg.connect(self._url) as conn:
            with conn.transaction():
                conn.execute(
                    sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(
                        sql.Identifier(self._schema)
                    )
                )
                # DO NOTHING then lock the canonical row: no duplicate IDs or authorization resets.
                conn.execute(
                    """INSERT INTO qtrade_user(user_id,issuer,subject,display_name,email)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT(issuer,subject) DO NOTHING""",
                    (
                        uuid4(),
                        identity.issuer,
                        identity.subject,
                        identity.display_name,
                        identity.email,
                    ),
                )
                with conn.cursor(row_factory=class_row(User)) as cursor:
                    cursor.execute(
                        "SELECT * FROM qtrade_user WHERE issuer=%s AND subject=%s FOR UPDATE",
                        (identity.issuer, identity.subject),
                    )
                    user = cursor.fetchone()
                    if user is None:
                        raise PortError(ErrorCode.UNAVAILABLE, "identity mapping unavailable")
                    if user.disabled:
                        raise PortError(ErrorCode.UNAUTHORIZED, "identity is disabled")
                    cursor.execute(
                        """UPDATE qtrade_user SET display_name=%s,email=coalesce(%s,email),
                        updated_at=now()
                        WHERE user_id=%s RETURNING *""",
                        (identity.display_name, identity.email, user.user_id),
                    )
                    user = cursor.fetchone()
                    if user is None:
                        raise PortError(ErrorCode.UNAVAILABLE, "identity mapping unavailable")
                created = conn.execute(
                    """INSERT INTO workspace(workspace_id,name,kind,owner_id)
                    VALUES (%s,%s,'personal',%s)
                    ON CONFLICT(owner_id) WHERE kind='personal' DO NOTHING
                    RETURNING workspace_id""",
                    (uuid4(), workspace_name, user.user_id),
                ).fetchone()
                with conn.cursor(row_factory=class_row(Workspace)) as cursor:
                    cursor.execute(
                        "SELECT * FROM workspace WHERE owner_id=%s AND kind='personal'",
                        (user.user_id,),
                    )
                    workspace = cursor.fetchone()
                    if workspace is None:
                        raise PortError(ErrorCode.UNAVAILABLE, "workspace mapping unavailable")
                if created is not None:
                    conn.execute(
                        """INSERT INTO membership(workspace_id,user_id,role,created_by)
                        VALUES (%s,%s,'admin',%s)""",
                        (workspace.workspace_id, user.user_id, user.user_id),
                    )
                with conn.cursor(row_factory=dict_row) as cursor:
                    cursor.execute(
                        "SELECT * FROM membership WHERE workspace_id=%s AND user_id=%s",
                        (workspace.workspace_id, user.user_id),
                    )
                    record = cursor.fetchone()
                    membership = (
                        Membership(**{**record, "role": Role(record["role"])}) if record else None
                    )
                return LoginIdentity(user, workspace, membership)
