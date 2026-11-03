"""Immutable dataset publication, atomic source links and current membership checks."""

import json
from uuid import UUID

import psycopg
from psycopg import sql

from qtrade_contracts import MarketDataset
from qtrade_contracts.models import Granularity
from qtrade_domain.authorization import Permission
from qtrade_domain.datasets import PublishedDataset
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.workspaces import PostgresWorkspaceStore


class PostgresDatasetRepository(PostgresStore):
    @staticmethod
    def _get(
        conn: psycopg.Connection[tuple[object, ...]], workspace_id: UUID, dataset_id: UUID
    ) -> PublishedDataset:
        row = conn.execute(
            """SELECT payload,request_sha256,parent_dataset_id,quality_report_sha256
            FROM market_dataset WHERE workspace_id=%s AND dataset_id=%s""",
            (workspace_id, dataset_id),
        ).fetchone()
        if row is None:
            raise PortError(ErrorCode.NOT_FOUND, "dataset unavailable")
        try:
            metadata = MarketDataset.model_validate_json(json.dumps(row[0]))
            if (
                not isinstance(row[1], str)
                or (row[2] is not None and not isinstance(row[2], UUID))
                or not isinstance(row[3], str)
            ):
                raise ValueError("invalid publication envelope")
            result = PublishedDataset(metadata, row[1], row[2], row[3])
        except (ValueError, TypeError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored dataset metadata") from None
        links = conn.execute(
            """SELECT batch_id,ordinal FROM market_dataset_batch
            WHERE workspace_id=%s AND dataset_id=%s ORDER BY ordinal""",
            (workspace_id, dataset_id),
        ).fetchall()
        if (
            metadata.workspace_id != workspace_id
            or metadata.dataset_id != dataset_id
            or tuple(row[0] for row in links) != metadata.batch_ids
            or tuple(row[1] for row in links) != tuple(range(len(metadata.batch_ids)))
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored dataset provenance")
        return result

    def get(self, workspace_id: UUID, dataset_id: UUID) -> PublishedDataset:
        with self._connection() as conn:
            return self._get(conn, workspace_id, dataset_id)

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[PublishedDataset, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid dataset page size")
        with self._connection() as conn:
            identifiers = conn.execute(
                """SELECT dataset_id FROM market_dataset WHERE workspace_id=%s
                ORDER BY jsonb_array_length(payload->'instrument_ids') > 1 DESC,
                jsonb_array_length(payload->'instrument_ids') DESC,
                published_at DESC,dataset_id DESC LIMIT %s""",
                (workspace_id, limit),
            ).fetchall()
            dataset_ids: list[UUID] = []
            for row in identifiers:
                dataset_id = row[0]
                if not isinstance(dataset_id, UUID):
                    raise PortError(ErrorCode.UNAVAILABLE, "invalid dataset identifier")
                dataset_ids.append(dataset_id)
            return tuple(self._get(conn, workspace_id, dataset_id) for dataset_id in dataset_ids)

    def for_instrument(
        self,
        workspace_id: UUID,
        instrument_id: str,
        *,
        granularity: Granularity | None = None,
        limit: int = 100,
        before: UUID | None = None,
    ) -> tuple[PublishedDataset, ...]:
        if (
            not isinstance(instrument_id, str)
            or not 1 <= len(instrument_id) <= 256
            or type(limit) is not int
            or not 1 <= limit <= 200
            or granularity not in (None, "tick", "1m", "5m", "15m", "30m", "1h", "1d")
            or (before is not None and not isinstance(before, UUID))
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid instrument dataset query")
        filters = [sql.SQL("workspace_id=%s"), sql.SQL("payload->'instrument_ids' @> %s::jsonb")]
        values: list[object] = [workspace_id, json.dumps([instrument_id])]
        if granularity is not None:
            filters.append(sql.SQL("payload->>'granularity'=%s"))
            values.append(granularity)
        if before is not None:
            filters.append(sql.SQL("dataset_id<%s"))
            values.append(before)
        values.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                sql.SQL(
                    "SELECT dataset_id FROM market_dataset WHERE {} "
                    "ORDER BY dataset_id DESC LIMIT %s"
                ).format(sql.SQL(" AND ").join(filters)),
                values,
            ).fetchall()
            identities: list[UUID] = []
            for row in rows:
                if not isinstance(row[0], UUID):
                    raise PortError(ErrorCode.UNAVAILABLE, "invalid dataset identifier")
                identities.append(row[0])
            return tuple(self._get(conn, workspace_id, identity) for identity in identities)

    def put(self, actor_id: UUID, dataset: PublishedDataset) -> None:
        item = dataset.metadata
        with self._connection() as conn:
            conn.execute(
                "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE",
                (item.workspace_id,),
            )
            access = PostgresWorkspaceStore._access(conn, actor_id, item.workspace_id)
            if access is None:
                raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
            access.require(Permission.DATA_MANAGE)
            try:
                existing = self._get(conn, item.workspace_id, item.dataset_id)
            except PortError as error:
                if error.code != ErrorCode.NOT_FOUND:
                    raise
            else:
                if existing != dataset:
                    raise PortError(ErrorCode.CONFLICT, "dataset metadata is immutable")
                return
            if dataset.parent_dataset_id is None:
                if item.version != 1:
                    raise PortError(ErrorCode.INVALID_INPUT, "invalid initial dataset version")
            else:
                parent = self._get(conn, item.workspace_id, dataset.parent_dataset_id).metadata
                if item.version != parent.version + 1 or item.batch_ids != parent.batch_ids:
                    raise PortError(ErrorCode.INVALID_INPUT, "invalid dataset replay lineage")
            conn.execute(
                """INSERT INTO market_dataset(workspace_id,dataset_id,parent_dataset_id,version,
                request_sha256,quality_report_sha256,payload,published_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (
                    item.workspace_id,
                    item.dataset_id,
                    dataset.parent_dataset_id,
                    item.version,
                    dataset.request_sha256,
                    dataset.quality_report_sha256,
                    item.model_dump_json(),
                    actor_id,
                ),
            )
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO market_dataset_batch(workspace_id,dataset_id,batch_id,ordinal)
                    VALUES (%s,%s,%s,%s)""",
                    [
                        (item.workspace_id, item.dataset_id, batch_id, ordinal)
                        for ordinal, batch_id in enumerate(item.batch_ids)
                    ],
                )
