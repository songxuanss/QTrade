"""Persistent watch ownership, explicit revision changes and expiring run fencing."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

import psycopg

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.freshness import FreshnessKey
from qtrade_data_contracts.freshness_watch import FreshnessWatch, FreshnessWatchControl
from qtrade_data_contracts.models import ErrorCode
from qtrade_data_core.freshness_refresh import FreshnessTransaction
from qtrade_data_core.freshness_watch import FreshnessWatchLease
from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.freshness import PostgresFreshness

_COLUMNS = "scope_id,watch_id,key_sha256,revision,enabled,next_due_at,payload"


class PostgresFreshnessWatches:
    def __init__(self, url: str, *, lease_seconds: int = 600):
        if type(lease_seconds) is not int or not 10 <= lease_seconds <= 900:
            raise ValueError("bounded watch lease required")
        self.url, self.lease_seconds = url, lease_seconds
        self.repository = PostgresFreshness(url)

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        with self.repository._connection() as conn:
            conn.execute("SET LOCAL lock_timeout='5s'")
            yield conn

    @staticmethod
    def _now(conn: psycopg.Connection[Any]) -> datetime:
        row = conn.execute("SELECT clock_timestamp()").fetchone()
        if row is None:
            raise DataFailure("unavailable")
        return row[0]  # type: ignore[no-any-return]

    @staticmethod
    def _read(row: tuple[Any, ...]) -> FreshnessWatch:
        try:
            value = FreshnessWatch.model_validate_json(json.dumps(row[6]))
            key_hash = digest(value.key.model_dump(mode="json"))
            if (row[0], row[1], row[2], row[3], row[4], row[5]) != (
                value.scope_id,
                value.watch_id,
                key_hash,
                value.revision,
                value.enabled,
                value.next_due_at,
            ) or value.watch_id != uuid5(value.scope_id, "freshness-watch:" + key_hash):
                raise ValueError("watch identity mismatch")
            return value
        except (TypeError, ValueError):
            raise DataFailure("unverified") from None

    @staticmethod
    def _save(
        conn: psycopg.Connection[Any],
        value: FreshnessWatch,
        action: str | None = None,
        action_actor: UUID | None = None,
    ) -> None:
        value = FreshnessWatch.model_validate_json(value.model_dump_json())
        body = value.model_dump(mode="json")
        conn.execute(
            """UPDATE qtrade_data.collection_freshness_watch SET revision=%s,
            enabled=%s,next_due_at=%s,lease_token=NULL,lease_expires_at=NULL,payload=%s::jsonb
            WHERE scope_id=%s AND watch_id=%s""",
            (
                value.revision,
                value.enabled,
                value.next_due_at,
                json.dumps(body),
                value.scope_id,
                value.watch_id,
            ),
        )
        if action is not None:
            conn.execute(
                """INSERT INTO qtrade_data.collection_freshness_watch_revision
                (scope_id,watch_id,revision,actor_id,action,payload_sha256,payload)
                VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    value.scope_id,
                    value.watch_id,
                    value.revision,
                    action_actor or value.actor_id,
                    action,
                    digest(body),
                    json.dumps(body),
                ),
            )

    def create(
        self, actor: UUID, publication: UUID, key: FreshnessKey, interval: int
    ) -> FreshnessWatch:
        key_hash = digest(key.model_dump(mode="json"))
        watch_id = uuid5(key.scope_id, "freshness-watch:" + key_hash)
        with self._connection() as conn:
            now = self._now(conn)
            value = FreshnessWatch(
                schema_version=1,
                scope_id=key.scope_id,
                watch_id=watch_id,
                actor_id=actor,
                key=key,
                reference_publication_id=publication,
                revision=1,
                enabled=False,
                interval_seconds=interval,
                created_at=now,
                updated_at=now,
                next_due_at=now,
            )
            inserted = conn.execute(
                """INSERT INTO qtrade_data.collection_freshness_watch
                (scope_id,watch_id,key_sha256,revision,enabled,next_due_at,payload)
                VALUES(%s,%s,%s,1,false,%s,%s::jsonb) ON CONFLICT(scope_id,key_sha256)
                DO NOTHING RETURNING watch_id""",
                (key.scope_id, watch_id, key_hash, now, value.model_dump_json()),
            ).fetchone()
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM qtrade_data.collection_freshness_watch "
                "WHERE scope_id=%s AND watch_id=%s",
                (key.scope_id, watch_id),
            ).fetchone()
            if row is None:
                raise DataFailure("unavailable")
            stored = self._read(row)
            if stored.key != key:
                raise DataFailure("conflict")
            # Existing configuration is not silently changed by duplicate create.
            if inserted:
                self._save(conn, stored, "created")
            return stored

    def get(self, scope: UUID, watch: UUID) -> FreshnessWatch:
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM qtrade_data.collection_freshness_watch "
                "WHERE scope_id=%s AND watch_id=%s",
                (scope, watch),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            return self._read(row)

    def control(
        self, actor: UUID, scope: UUID, watch: UUID, body: FreshnessWatchControl
    ) -> FreshnessWatch:
        body = FreshnessWatchControl.model_validate_json(body.model_dump_json())
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM qtrade_data.collection_freshness_watch "
                "WHERE scope_id=%s AND watch_id=%s FOR UPDATE",
                (scope, watch),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            before = self._read(row)
            if before.revision != body.revision:
                raise DataFailure("conflict")
            now = self._now(conn)
            value = before.model_copy(
                update=dict(
                    revision=before.revision + 1,
                    enabled=body.enabled,
                    actor_id=actor if body.enabled else before.actor_id,
                    updated_at=now,
                    next_due_at=now,
                    paused_reason=None,
                )
            )
            self._save(conn, value, "enabled" if body.enabled else "disabled", actor)
            return value

    def claim(self) -> FreshnessWatchLease | None:
        with self._connection() as conn:
            row = conn.execute(f"""SELECT {_COLUMNS} FROM qtrade_data.collection_freshness_watch
                WHERE enabled AND next_due_at<=clock_timestamp()
                AND (lease_expires_at IS NULL OR lease_expires_at<=clock_timestamp())
                ORDER BY next_due_at,scope_id,watch_id LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
            if row is None:
                return None
            value = self._read(row)
            token = uuid4()
            expires = conn.execute(
                """UPDATE qtrade_data.collection_freshness_watch
                SET lease_token=%s,lease_expires_at=clock_timestamp()+(%s*interval '1 second')
                WHERE scope_id=%s AND watch_id=%s RETURNING lease_expires_at""",
                (token, self.lease_seconds, value.scope_id, value.watch_id),
            ).fetchone()
            if expires is None:
                raise DataFailure("unavailable")
            return FreshnessWatchLease(value, token, expires[0])

    @staticmethod
    def require_lease(conn: psycopg.Connection[Any], lease: FreshnessWatchLease) -> FreshnessWatch:
        row = conn.execute(
            f"""SELECT {_COLUMNS},lease_token,lease_expires_at>clock_timestamp()
            FROM qtrade_data.collection_freshness_watch
            WHERE scope_id=%s AND watch_id=%s FOR UPDATE""",
            (lease.watch.scope_id, lease.watch.watch_id),
        ).fetchone()
        if row is None or row[7] != lease.token or row[8] is not True:
            raise DataFailure("conflict")
        value = PostgresFreshnessWatches._read(row)
        if (
            not value.enabled
            or value.revision != lease.watch.revision
            or value.actor_id != lease.watch.actor_id
            or value.key != lease.watch.key
        ):
            raise DataFailure("conflict")
        return value

    def finish(
        self,
        lease: FreshnessWatchLease,
        state: Literal["completed", "deferred", "paused"],
        error: ErrorCode | None,
        checked_at: datetime | None,
    ) -> bool:
        with self._connection() as conn:
            try:
                value = self.require_lease(conn, lease)
            except DataFailure as exc:
                if exc.code == "conflict":
                    return False
                raise
            now = self._now(conn)
            updated = value.model_copy(
                update=dict(
                    updated_at=now,
                    last_attempt_at=now,
                    next_due_at=now + timedelta(seconds=value.interval_seconds),
                    last_checked_at=checked_at if state == "completed" else value.last_checked_at,
                    last_error_code=error,
                    enabled=state != "paused",
                    paused_reason=error if state == "paused" else None,
                    revision=value.revision + 1 if state == "paused" else value.revision,
                )
            )
            self._save(conn, updated, "paused" if state == "paused" else None)
            return True


class WatchedFreshness(PostgresFreshness):
    """A watch lease fences the entire snapshot/outbox commit, not just run status."""

    def __init__(self, watches: PostgresFreshnessWatches, lease: FreshnessWatchLease):
        super().__init__(watches.url)
        self.watches, self.lease = watches, lease

    @contextmanager
    def atomic(self, key: FreshnessKey) -> Iterator[FreshnessTransaction]:
        if key != self.lease.watch.key:
            raise DataFailure("conflict")
        with self.watches._connection() as conn:
            self.watches.require_lease(conn, self.lease)
            with PostgresFreshness(self.url, conn).atomic(key) as transaction:
                yield transaction
            # Auth/archive work can outlast a lease. Expiry here must roll back
            # snapshot and outbox, even if a write succeeded inside the context.
            self.watches.require_lease(conn, self.lease)
