"""User/workspace-isolated typed drafts with live membership revalidation."""

import json
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from qtrade_contracts.collection_preferences import CollectionPreferences
from qtrade_domain.authorization import Permission
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.workspaces import PostgresWorkspaceStore


class PostgresCollectionPreferences(PostgresWorkspaceStore):
    def get(self, actor: UUID, scope: UUID) -> CollectionPreferences | None:
        with self._connection() as conn:
            access = self._access(conn, actor, scope)
            if access is None:
                raise PortError(ErrorCode.UNAUTHORIZED, "workspace access revoked")
            access.require(Permission.READ)
            row = conn.execute(
                "SELECT draft FROM collection_preferences WHERE user_id=%s AND workspace_id=%s",
                (actor, scope),
            ).fetchone()
            if row is None:
                return None
            try:
                return CollectionPreferences.model_validate_json(json.dumps(row[0]))
            except ValidationError:
                raise PortError(ErrorCode.UNAVAILABLE, "saved collection draft invalid") from None

    def put(self, actor: UUID, scope: UUID, draft: CollectionPreferences) -> None:
        with self._connection() as conn:
            # Serialize with member changes and recheck current authority, not stale UI role.
            conn.execute(
                "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE", (scope,)
            )
            access = self._access(conn, actor, scope)
            if access is None:
                raise PortError(ErrorCode.UNAUTHORIZED, "workspace access revoked")
            access.require(Permission.DATA_MANAGE)
            conn.execute(
                """INSERT INTO collection_preferences(user_id,workspace_id,draft) VALUES(%s,%s,%s)
                ON CONFLICT(user_id,workspace_id) DO UPDATE
                SET draft=excluded.draft,updated_at=now()""",
                (actor, scope, Jsonb(draft.model_dump(mode="json"))),
            )
