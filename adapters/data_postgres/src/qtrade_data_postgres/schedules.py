"""Scoped persistent schedule controls and short fenced ownership leases."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

import psycopg
from pydantic import ValidationError

from qtrade_data_contracts.schedule_dispatch import ScheduleDispatchDecision
from qtrade_data_contracts.schedules import CollectionScheduleDraft, CollectionScheduleRecord
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.schedules import ScheduleLease


def aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataFailure("invalid_input")


class PostgresSchedules:
    def __init__(self, database_url: str):
        self.url = database_url

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            with psycopg.connect(self.url) as conn:
                yield conn
        except psycopg.Error:
            # Database exception text may contain private connection details.
            raise DataFailure("unavailable") from None

    @staticmethod
    def _record(row: tuple[Any, ...]) -> CollectionScheduleRecord:
        draft = CollectionScheduleDraft.model_validate_json(json.dumps(row[0]))
        return CollectionScheduleRecord(
            schema_version=1,
            scope_id=draft.scope_id,
            draft=draft,
            revision=row[1],
            state=row[2],
            pause_reason=row[3],
            next_due_at=row[4],
            updated_at=row[5],
        )

    @staticmethod
    def _get(
        conn: psycopg.Connection[Any], scope: UUID, schedule: UUID
    ) -> CollectionScheduleRecord:
        row = conn.execute(
            "SELECT draft,revision,state,pause_reason,next_due_at,updated_at "
            "FROM qtrade_data.collection_schedule WHERE scope_id=%s AND schedule_id=%s",
            (scope, schedule),
        ).fetchone()
        if row is None:
            raise DataFailure("not_found")
        return PostgresSchedules._record(row)

    def create(
        self, draft: CollectionScheduleDraft, next_due: datetime
    ) -> CollectionScheduleRecord:
        aware(next_due)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO qtrade_data.collection_schedule "
                "(scope_id,schedule_id,draft,revision,next_due_at) VALUES(%s,%s,%s::jsonb,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (
                    draft.scope_id,
                    draft.schedule_id,
                    draft.model_dump_json(),
                    draft.revision,
                    next_due,
                ),
            )
            stored = self._get(conn, draft.scope_id, draft.schedule_id)
            if stored.draft != draft:
                raise DataFailure("conflict")
            return stored

    def get(self, scope: UUID, schedule: UUID) -> CollectionScheduleRecord:
        with self._connection() as conn:
            return self._get(conn, scope, schedule)

    def list(
        self, scope: UUID, *, limit: int = 100, offset: int = 0
    ) -> tuple[CollectionScheduleRecord, ...]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 1000000
        ):
            raise DataFailure("invalid_input")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT draft,revision,state,pause_reason,next_due_at,updated_at "
                "FROM qtrade_data.collection_schedule WHERE scope_id=%s "
                "ORDER BY updated_at DESC,schedule_id LIMIT %s OFFSET %s",
                (scope, limit, offset),
            ).fetchall()
            return tuple(self._record(row) for row in rows)

    @staticmethod
    def _dispatch(row: tuple[Any, ...]) -> ScheduleDispatchDecision:
        try:
            decision = ScheduleDispatchDecision.model_validate_json(json.dumps(row[0]))
        except ValidationError:
            raise DataFailure("unverified") from None
        if decision.content_sha256 != row[1]:
            raise DataFailure("unverified")
        return decision

    def get_dispatch(self, scope: UUID, run: UUID) -> ScheduleDispatchDecision:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT d.payload,d.content_sha256,r.schedule_id,r.revision,r.due_at "
                "FROM qtrade_data.collection_schedule_dispatch d "
                "JOIN qtrade_data.collection_schedule_run r USING(scope_id,run_id) "
                "WHERE d.scope_id=%s AND d.run_id=%s",
                (scope, run),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            decision = self._dispatch(row)
            if (
                decision.scope_id != scope
                or decision.run_id != run
                or decision.schedule_id != row[2]
                or decision.revision != row[3]
                or decision.due_at != row[4]
            ):
                raise DataFailure("unverified")
            return decision

    def freeze_dispatch(
        self, lease: ScheduleLease, decision: ScheduleDispatchDecision, now: datetime
    ) -> ScheduleDispatchDecision:
        """First intent wins; repeating it may not move its range or evidence."""
        aware(now)
        try:
            decision.check_schedule(lease.record.draft)
        except ValueError:
            raise DataFailure("conflict") from None
        if decision.due_at != lease.record.next_due_at:
            raise DataFailure("conflict")
        draft = lease.record.draft
        with self._connection() as conn:
            owned = conn.execute(
                "SELECT s.lease_until FROM qtrade_data.collection_schedule s "
                "JOIN qtrade_data.collection_schedule_run r "
                "ON r.scope_id=s.scope_id AND r.schedule_id=s.schedule_id "
                "AND r.revision=s.revision AND r.due_at=s.next_due_at "
                "WHERE s.scope_id=%s AND s.schedule_id=%s AND s.state='enabled' "
                "AND s.revision=%s AND s.lease_id=%s AND s.next_due_at=%s "
                "AND r.run_id=%s FOR UPDATE OF s",
                (
                    draft.scope_id,
                    draft.schedule_id,
                    lease.record.revision,
                    lease.token,
                    lease.record.next_due_at,
                    decision.run_id,
                ),
            ).fetchone()
            current = conn.execute("SELECT clock_timestamp()").fetchone()
            # Check after any row-lock wait; a stale caller clock cannot extend
            # ownership, and expiry blocks new freezing even after recovery.
            if owned is None or current is None or owned[0] <= max(now, current[0]):
                raise DataFailure("conflict")
            conn.execute(
                "INSERT INTO qtrade_data.collection_schedule_dispatch "
                "(scope_id,run_id,payload,content_sha256) VALUES(%s,%s,%s::jsonb,%s) "
                "ON CONFLICT DO NOTHING",
                (
                    draft.scope_id,
                    decision.run_id,
                    decision.model_dump_json(),
                    decision.content_sha256,
                ),
            )
            row = conn.execute(
                "SELECT payload,content_sha256 FROM qtrade_data.collection_schedule_dispatch "
                "WHERE scope_id=%s AND run_id=%s",
                (draft.scope_id, decision.run_id),
            ).fetchone()
            if row is None:
                raise DataFailure("unavailable")
            stored = self._dispatch(row)
            # A retry may have a later wall-clock freeze timestamp. Preserve
            # the first timestamp; every other semantic field must match.
            if stored.model_dump(exclude={"frozen_at"}) != decision.model_dump(
                exclude={"frozen_at"}
            ):
                raise DataFailure("conflict")
            return stored

    def control(
        self, scope: UUID, schedule: UUID, revision: str, state: Literal["disabled", "enabled"]
    ) -> CollectionScheduleRecord:
        if state not in {"disabled", "enabled"}:
            raise DataFailure("invalid_input")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT revision,state FROM qtrade_data.collection_schedule "
                "WHERE scope_id=%s AND schedule_id=%s FOR UPDATE",
                (scope, schedule),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            if row[0] != revision:
                raise DataFailure("conflict")
            if row[1] == state:
                return self._get(conn, scope, schedule)
            # A disable invalidates ownership in the same transaction. Old
            # dispatchers cannot advance this plan after control is acknowledged.
            conn.execute(
                "UPDATE qtrade_data.collection_schedule SET state=%s,pause_reason=NULL,"
                "lease_id=NULL,lease_until=NULL,updated_at=now() "
                "WHERE scope_id=%s AND schedule_id=%s",
                (state, scope, schedule),
            )
            return self._get(conn, scope, schedule)

    def claim(self, now: datetime, *, lease_seconds: int = 60) -> ScheduleLease | None:
        aware(now)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 600:
            raise DataFailure("invalid_input")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT scope_id,schedule_id FROM qtrade_data.collection_schedule "
                "WHERE state='enabled' AND next_due_at<=%s AND "
                "(lease_id IS NULL OR lease_until<=%s) ORDER BY next_due_at,scope_id,schedule_id "
                "FOR UPDATE SKIP LOCKED LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            scope, schedule = row
            token, until = uuid4(), now + timedelta(seconds=lease_seconds)
            conn.execute(
                "UPDATE qtrade_data.collection_schedule SET lease_id=%s,lease_until=%s "
                "WHERE scope_id=%s AND schedule_id=%s",
                (token, until, scope, schedule),
            )
            return ScheduleLease(self._get(conn, scope, schedule), token, until)

    def advance(self, lease: ScheduleLease, next_due: datetime, now: datetime) -> None:
        aware(now)
        aware(next_due)
        if next_due <= lease.record.next_due_at:
            raise DataFailure("invalid_input")
        draft = lease.record.draft
        with self._connection() as conn:
            result = conn.execute(
                "UPDATE qtrade_data.collection_schedule SET next_due_at=%s,"
                "lease_id=NULL,lease_until=NULL,updated_at=%s WHERE scope_id=%s AND schedule_id=%s "
                "AND state='enabled' AND revision=%s AND lease_id=%s AND lease_until>%s "
                "AND lease_until>now()",
                (
                    next_due,
                    now,
                    draft.scope_id,
                    draft.schedule_id,
                    lease.record.revision,
                    lease.token,
                    now,
                ),
            )
            if result.rowcount != 1:
                raise DataFailure("conflict")

    def reserve_run(self, lease: ScheduleLease, now: datetime) -> UUID:
        aware(now)
        draft = lease.record.draft
        due = lease.record.next_due_at
        # Equivalent offsets share identity; lease recovery does not change the slot.
        run = uuid5(
            draft.schedule_id, lease.record.revision + ":" + due.astimezone(UTC).isoformat()
        )
        with self._connection() as conn:
            owned = conn.execute(
                "SELECT 1 FROM qtrade_data.collection_schedule "
                "WHERE scope_id=%s AND schedule_id=%s AND state='enabled' AND revision=%s "
                "AND next_due_at=%s AND lease_id=%s AND lease_until>%s AND lease_until>now() "
                "FOR UPDATE",
                (draft.scope_id, draft.schedule_id, lease.record.revision, due, lease.token, now),
            ).fetchone()
            if owned is None:
                raise DataFailure("conflict")
            conn.execute(
                "INSERT INTO qtrade_data.collection_schedule_run "
                "(scope_id,run_id,schedule_id,revision,due_at) VALUES(%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (draft.scope_id, run, draft.schedule_id, lease.record.revision, due),
            )
            row = conn.execute(
                "SELECT run_id FROM qtrade_data.collection_schedule_run "
                "WHERE scope_id=%s AND schedule_id=%s AND revision=%s AND due_at=%s",
                (draft.scope_id, draft.schedule_id, lease.record.revision, due),
            ).fetchone()
            if row is None or row[0] != run:
                raise DataFailure("conflict")
            return run

    def pause(
        self,
        lease: ScheduleLease,
        reason: Literal["authorization", "source_unavailable", "approval_required"],
        now: datetime,
    ) -> None:
        aware(now)
        if reason not in {"authorization", "source_unavailable", "approval_required"}:
            raise DataFailure("invalid_input")
        draft = lease.record.draft
        # A delayed authorization response must not pause a newer owner or a
        # re-enabled revision. Both caller and database clocks fence expiration.
        with self._connection() as conn:
            result = conn.execute(
                "UPDATE qtrade_data.collection_schedule SET state='paused',pause_reason=%s,"
                "lease_id=NULL,lease_until=NULL,updated_at=%s WHERE scope_id=%s AND schedule_id=%s "
                "AND state='enabled' AND revision=%s AND lease_id=%s "
                "AND lease_until>%s AND lease_until>now()",
                (
                    reason,
                    now,
                    draft.scope_id,
                    draft.schedule_id,
                    lease.record.revision,
                    lease.token,
                    now,
                ),
            )
            if result.rowcount != 1:
                raise DataFailure("conflict")
