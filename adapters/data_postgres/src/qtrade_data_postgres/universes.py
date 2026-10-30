"""Append-only member snapshots with scoped paging and atomic version allocation."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID, uuid4

import psycopg

from qtrade_data_contracts import UniverseContent, UniverseSnapshot
from qtrade_data_core.ports import DataFailure


class PostgresUniverses:
    def __init__(self, database_url: str):
        self._database_url = database_url

    @contextmanager
    def _connection(self, scope_id: UUID) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
        try:
            with psycopg.connect(self._database_url) as conn:
                conn.execute("SELECT set_config('qtrade_data.scope_id',%s,true)", (str(scope_id),))
                yield conn
        except psycopg.Error:
            raise DataFailure("unavailable") from None

    @staticmethod
    def _snapshot(payload: object, scope_id: UUID) -> UniverseSnapshot:
        try:
            result = UniverseSnapshot.model_validate_json(json.dumps(payload))
            if result.scope_id != scope_id:
                raise ValueError
            return result
        except (TypeError, ValueError):
            raise DataFailure("unavailable") from None

    def append(self, actor_id: UUID, content: UniverseContent) -> UniverseSnapshot:
        with self._connection(content.scope_id) as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (str(content.scope_id) + ":" + content.universe_id,),
            )
            row = conn.execute(
                """SELECT coalesce(max(version),0) FROM qtrade_data.universe_snapshot
                WHERE scope_id=%s AND universe_id=%s""",
                (content.scope_id, content.universe_id),
            ).fetchone()
            if row is None or type(row[0]) is not int:
                raise DataFailure("unavailable")
            body = content.model_dump()
            body["version"] = row[0] + 1
            snapshot = UniverseSnapshot.freeze(uuid4(), UniverseContent.model_validate(body))
            conn.execute(
                """INSERT INTO qtrade_data.universe_snapshot
                (scope_id,snapshot_id,universe_id,version,observed_at,created_by,content_sha256,payload)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    snapshot.scope_id,
                    snapshot.snapshot_id,
                    snapshot.universe_id,
                    snapshot.version,
                    snapshot.observed_at,
                    actor_id,
                    snapshot.content_sha256,
                    snapshot.model_dump_json(),
                ),
            )
            return snapshot

    def get(self, scope_id: UUID, snapshot_id: UUID) -> UniverseSnapshot:
        with self._connection(scope_id) as conn:
            row = conn.execute(
                """SELECT payload FROM qtrade_data.universe_snapshot
                WHERE scope_id=%s AND snapshot_id=%s""",
                (scope_id, snapshot_id),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            snapshot = self._snapshot(row[0], scope_id)
            if snapshot.snapshot_id != snapshot_id:
                raise DataFailure("unavailable")
            return snapshot

    def list(
        self, scope_id: UUID, *, limit: int, before: tuple[datetime, UUID] | None = None
    ) -> tuple[UniverseSnapshot, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DataFailure("invalid_input")
        with self._connection(scope_id) as conn:
            if before is None:
                rows = conn.execute(
                    """SELECT payload FROM qtrade_data.universe_snapshot
                    WHERE scope_id=%s ORDER BY observed_at DESC,snapshot_id DESC LIMIT %s""",
                    (scope_id, limit),
                ).fetchall()
            else:
                if before[0].tzinfo is None or not isinstance(before[1], UUID):
                    raise DataFailure("invalid_input")
                rows = conn.execute(
                    """SELECT payload FROM qtrade_data.universe_snapshot
                    WHERE scope_id=%s AND (observed_at,snapshot_id)<(%s,%s)
                    ORDER BY observed_at DESC,snapshot_id DESC LIMIT %s""",
                    (scope_id, *before, limit),
                ).fetchall()
            return tuple(self._snapshot(row[0], scope_id) for row in rows)
