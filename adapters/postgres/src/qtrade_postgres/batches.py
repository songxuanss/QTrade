"""Immutable raw-batch metadata with transaction-local membership authorization."""

import json
from uuid import UUID

from qtrade_contracts import DataBatch
from qtrade_domain.authorization import Permission
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.workspaces import PostgresWorkspaceStore


def metadata(payload: object) -> DataBatch:
    try:
        return DataBatch.model_validate_json(json.dumps(payload))
    except (ValueError, TypeError):
        raise PortError(ErrorCode.UNAVAILABLE, "invalid stored raw batch metadata") from None


class PostgresBatchRepository(PostgresStore):
    def put(self, actor_id: UUID, batch: DataBatch) -> None:
        with self._connection() as conn:
            conn.execute(
                "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE",
                (batch.workspace_id,),
            )
            access = PostgresWorkspaceStore._access(conn, actor_id, batch.workspace_id)
            if access is None:
                raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
            access.require(Permission.DATA_MANAGE)
            existing = conn.execute(
                "SELECT payload FROM raw_data_batch WHERE workspace_id=%s AND batch_id=%s",
                (batch.workspace_id, batch.batch_id),
            ).fetchone()
            if existing is not None:
                if metadata(existing[0]) != batch:
                    raise PortError(ErrorCode.CONFLICT, "batch metadata is immutable")
                return
            conn.execute(
                """INSERT INTO raw_data_batch(workspace_id,batch_id,source,ingestion_task_id,
                content_sha256,payload,published_by) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (
                    batch.workspace_id,
                    batch.batch_id,
                    batch.source,
                    batch.ingestion_task_id,
                    batch.content_sha256,
                    batch.model_dump_json(),
                    actor_id,
                ),
            )

    def get(self, workspace_id: UUID, batch_id: UUID) -> DataBatch:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM raw_data_batch WHERE workspace_id=%s AND batch_id=%s",
                (workspace_id, batch_id),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.NOT_FOUND, "batch unavailable")
        result = metadata(row[0])
        if result.workspace_id != workspace_id or result.batch_id != batch_id:
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored batch scope")
        return result

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[DataBatch, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid batch page size")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT payload FROM raw_data_batch WHERE workspace_id=%s
                ORDER BY (payload->>'collected_at')::timestamptz DESC,batch_id DESC LIMIT %s""",
                (workspace_id, limit),
            ).fetchall()
        result = tuple(metadata(row[0]) for row in rows)
        if any(batch.workspace_id != workspace_id for batch in result):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid batch list scope")
        return result
