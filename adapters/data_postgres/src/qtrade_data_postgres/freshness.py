"""Scoped immutable freshness snapshots with content verification on every read."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

import psycopg

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.freshness import FreshnessKey, FreshnessResult
from qtrade_data_contracts.freshness_notice import FreshnessNotice
from qtrade_data_core.freshness_notice import transition_notice
from qtrade_data_core.freshness_refresh import FreshnessTransaction
from qtrade_data_core.ports import DataFailure


class PostgresFreshness:
    def __init__(
        self,
        database_url: str,
        connection: psycopg.Connection[Any] | None = None,
        locked_key: str | None = None,
        lock_wait_ms: int = 5000,
    ):
        if type(lock_wait_ms) is not int or not 1 <= lock_wait_ms <= 30000:
            raise ValueError("bounded freshness lock wait required")
        self.url, self.connection, self.locked_key = database_url, connection, locked_key
        self.lock_wait_ms = lock_wait_ms

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            if self.connection is not None:
                yield self.connection
            else:
                with psycopg.connect(self.url, connect_timeout=5) as conn:
                    yield conn
        except psycopg.Error:
            raise DataFailure("unavailable") from None

    @staticmethod
    def _lock(conn: psycopg.Connection[Any], key_hash: str, wait_ms: int) -> None:
        # A paused owner must not block every subsequent refresh indefinitely.
        conn.execute("SELECT set_config('lock_timeout', %s, true)", (f"{wait_ms}ms",))
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,794616387332))",
            ("freshness:" + key_hash,),
        )

    @contextmanager
    def atomic(self, key: FreshnessKey) -> Iterator[FreshnessTransaction]:
        key_hash = digest(key.model_dump(mode="json"))
        if self.locked_key is not None and self.locked_key != key_hash:
            raise DataFailure("conflict")
        with self._connection() as conn:
            self._lock(conn, key_hash, self.lock_wait_ms)
            yield PostgresFreshness(self.url, conn, key_hash, self.lock_wait_ms)

    def _bound(self, key: FreshnessKey) -> None:
        if self.locked_key is not None and self.locked_key != digest(key.model_dump(mode="json")):
            raise DataFailure("conflict")

    @staticmethod
    def _canonical(result: FreshnessResult) -> dict[str, Any]:
        value: dict[str, Any] = result.model_dump(mode="json")
        # Equivalent offset representations denote one observation instant.
        # Canonical UTC prevents duplicates or false conflicts after restart.
        value["checked_at"] = result.checked_at.astimezone(UTC).isoformat()
        if result.expectation:
            value["expectation"]["available_at"] = result.expectation.available_at.astimezone(
                UTC
            ).isoformat()
        for name in ("source", "collected", "published"):
            point = getattr(result.evidence, name)
            if point is not None:
                value["evidence"][name]["observed_at"] = point.observed_at.astimezone(
                    UTC
                ).isoformat()
        return value

    @classmethod
    def _read(cls, row: tuple[Any, ...], key: FreshnessKey) -> FreshnessResult:
        try:
            result = FreshnessResult.model_validate_json(json.dumps(row[4]))
            content = digest(cls._canonical(result))
            if (
                result.key != key
                or result.scope_id != key.scope_id
                or row[0] != uuid5(key.scope_id, content)
                or row[1] != digest(key.model_dump(mode="json"))
                or row[2] != result.checked_at
                or row[3] != content
            ):
                raise ValueError("invalid freshness snapshot")
            return result
        except (ValueError, TypeError):
            raise DataFailure("unverified") from None

    def append(self, result: FreshnessResult) -> FreshnessResult:
        try:
            result = FreshnessResult.model_validate_json(result.model_dump_json())
        except ValueError:
            raise DataFailure("invalid_input") from None
        self._bound(result.key)
        value = self._canonical(result)
        content, key_hash = digest(value), digest(result.key.model_dump(mode="json"))
        with self._connection() as conn:
            # Archival appends share the same lock; they cannot interleave an
            # atomic refresh's read and commit. Live producers use the core merge.
            self._lock(conn, key_hash, self.lock_wait_ms)
            conn.execute(
                """INSERT INTO qtrade_data.collection_freshness_snapshot
                (scope_id,snapshot_id,key_sha256,checked_at,result_sha256,payload)
                VALUES(%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT(scope_id,key_sha256,checked_at) DO NOTHING""",
                (
                    result.scope_id,
                    uuid5(result.scope_id, content),
                    key_hash,
                    result.checked_at,
                    content,
                    json.dumps(value),
                ),
            )
            row = conn.execute(
                """SELECT snapshot_id,key_sha256,checked_at,result_sha256,payload
                FROM qtrade_data.collection_freshness_snapshot
                WHERE scope_id=%s AND key_sha256=%s AND checked_at=%s""",
                (result.scope_id, key_hash, result.checked_at),
            ).fetchone()
            if row is None:
                raise DataFailure("unavailable")
            stored = self._read(row, result.key)
            if digest(self._canonical(stored)) != content:
                raise DataFailure("conflict")
            return stored

    def latest(self, key: FreshnessKey) -> FreshnessResult:
        self._bound(key)
        with self._connection() as conn:
            row = conn.execute(
                """SELECT snapshot_id,key_sha256,checked_at,result_sha256,payload
                FROM qtrade_data.collection_freshness_snapshot
                WHERE scope_id=%s AND key_sha256=%s ORDER BY checked_at DESC LIMIT 1""",
                (key.scope_id, digest(key.model_dump(mode="json"))),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            return self._read(row, key)

    @classmethod
    def _notice_payload(cls, notice: FreshnessNotice) -> dict[str, Any]:
        value: dict[str, Any] = notice.model_dump(mode="json")
        value["result"] = cls._canonical(notice.result)
        return value

    @classmethod
    def _read_notice(cls, row: tuple[Any, ...], key: FreshnessKey) -> FreshnessNotice:
        try:
            notice = FreshnessNotice.model_validate_json(json.dumps(row[7]))
            if (
                notice.result.key != key
                or row[0] != notice.notice_id
                or row[1] != digest(key.model_dump(mode="json"))
                or row[2] != notice.episode_id
                or row[3] != notice.kind
                or row[4] != notice.occurred_at
                or row[5] != uuid5(key.scope_id, digest(cls._canonical(notice.result)))
                or row[6] != digest(cls._notice_payload(notice))
            ):
                raise ValueError("notice identity or content mismatch")
            return notice
        except (ValueError, TypeError):
            raise DataFailure("unverified") from None

    def notices(self, key: FreshnessKey, *, limit: int = 50) -> tuple[FreshnessNotice, ...]:
        """Scoped immutable transitions, newest first; no delivery is acknowledged here."""
        self._bound(key)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DataFailure("invalid_input")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT notice_id,key_sha256,episode_id,kind,occurred_at,
                snapshot_id,notice_sha256,payload FROM qtrade_data.collection_freshness_notice
                WHERE scope_id=%s AND key_sha256=%s ORDER BY sequence DESC LIMIT %s""",
                (key.scope_id, digest(key.model_dump(mode="json")), limit),
            ).fetchall()
            return tuple(self._read_notice(row, key) for row in rows)

    def notify(self, actor: UUID, result: FreshnessResult) -> None:
        """Append an incident transition within the already locked refresh transaction.

        Reading the last *notice* retains an open episode through unknown/grace
        checks. Only current evidence closes it. There is no supplier or network
        I/O inside this transaction, and an insert failure rolls back the check.
        """
        if self.connection is None or self.locked_key is None:
            raise DataFailure("conflict")
        self._bound(result.key)
        if self.latest(result.key) != result:
            raise DataFailure("conflict")
        previous = self.notices(result.key, limit=1)
        notice = transition_notice(actor, result, previous[0] if previous else None)
        if notice is None:
            return
        body = self._notice_payload(notice)
        self.connection.execute(
            """INSERT INTO qtrade_data.collection_freshness_notice
            (scope_id,notice_id,key_sha256,episode_id,kind,occurred_at,
                snapshot_id,notice_sha256,payload)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                notice.scope_id,
                notice.notice_id,
                self.locked_key,
                notice.episode_id,
                notice.kind,
                notice.occurred_at,
                uuid5(result.scope_id, digest(self._canonical(result))),
                digest(body),
                json.dumps(body),
            ),
        )
        if self.notices(result.key, limit=1) != (notice,):
            raise DataFailure("unavailable")

    def history(
        self, key: FreshnessKey, *, limit: int = 50, before: datetime | None = None
    ) -> tuple[FreshnessResult, ...]:
        self._bound(key)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DataFailure("invalid_input")
        if before is not None and before.utcoffset() is None:
            raise DataFailure("invalid_input")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT snapshot_id,key_sha256,checked_at,result_sha256,payload
                FROM qtrade_data.collection_freshness_snapshot
                WHERE scope_id=%s AND key_sha256=%s
                AND (%s::timestamptz IS NULL OR checked_at<%s)
                ORDER BY checked_at DESC LIMIT %s""",
                (key.scope_id, digest(key.model_dump(mode="json")), before, before, limit),
            ).fetchall()
            return tuple(self._read(row, key) for row in rows)
