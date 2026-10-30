"""Scheduled admission through the ordinary collection repository interface.

The final ownership check and job insertion share one transaction. Provider
planning happens before the schedule row lock; disable therefore waits only
for admission/commit, not for metadata HTTP. This adapter is not a timer.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg

from qtrade_data_contracts.collection import CollectionJobRequest, FactorDefinition
from qtrade_data_contracts.schedule_dispatch import MarketDispatchInput
from qtrade_data_contracts.schedules import MarketScheduleSelection
from qtrade_data_core.collection import CollectionPart, JobRepository
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.schedules import ScheduleLease
from qtrade_data_postgres.jobs import PostgresJobs
from qtrade_data_postgres.schedules import PostgresSchedules


class PostgresScheduledJobs(PostgresJobs):
    """Bind one reserved slot to normal submission, fencing only new inserts.

    Already accepted requests remain idempotently readable after disable, but
    this adapter cannot admit another ID, actor, source or selection. Execution
    still rechecks current actor permission through the existing Worker.
    """

    def __init__(
        self,
        database_url: str,
        lease: ScheduleLease,
        run_id: UUID,
        conn: psycopg.Connection[Any] | None = None,
    ):
        super().__init__(database_url, conn)
        self.lease, self.run_id = lease, run_id

    @contextmanager
    def atomic(self, scope_id: UUID) -> Iterator[JobRepository]:
        if scope_id != self.lease.record.scope_id:
            raise DataFailure("conflict")
        with self._connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(scope_id),))
            yield PostgresScheduledJobs(self.url, self.lease, self.run_id, conn)

    def submit(
        self,
        job_id: UUID,
        request: CollectionJobRequest,
        parts: tuple[CollectionPart, ...],
        definitions: tuple[FactorDefinition, ...],
        names: dict[str, str],
    ) -> dict[str, object]:
        draft = self.lease.record.draft
        selection = draft.selection
        if (
            self.conn is None
            or not isinstance(selection, MarketScheduleSelection)
            or job_id != self.run_id
            or request.request_id != self.run_id
            or request.scope_id != draft.scope_id
            or request.actor_id != draft.actor_id
            or request.source != selection.source
            or request.dataset_kind != selection.dataset_kind
            or request.granularity != selection.granularity
            or request.instrument_ids != selection.instrument_ids
            or request.fields != selection.fields
            or request.mode != "missing"
        ):
            raise DataFailure("conflict")
        with self._connection() as conn:
            # clock_timestamp(), unlike now(), advances within a transaction.
            # Slow preplanning must not admit a lease that expired meanwhile.
            owned = conn.execute(
                "SELECT s.lease_until,d.payload,d.content_sha256 "
                "FROM qtrade_data.collection_schedule s "
                "JOIN qtrade_data.collection_schedule_run r "
                "ON r.scope_id=s.scope_id AND r.schedule_id=s.schedule_id "
                "AND r.revision=s.revision AND r.due_at=s.next_due_at "
                "JOIN qtrade_data.collection_schedule_dispatch d "
                "ON d.scope_id=r.scope_id AND d.run_id=r.run_id "
                "WHERE s.scope_id=%s AND s.schedule_id=%s AND s.state='enabled' "
                "AND s.revision=%s AND s.lease_id=%s AND s.lease_until>clock_timestamp() "
                "AND r.run_id=%s AND s.next_due_at=%s FOR UPDATE OF s",
                (
                    draft.scope_id,
                    draft.schedule_id,
                    self.lease.record.revision,
                    self.lease.token,
                    self.run_id,
                    self.lease.record.next_due_at,
                ),
            ).fetchone()
            if owned is None:
                raise DataFailure("conflict")
            # A row lock may wait without any row update, in which case SQL's
            # earlier WHERE predicate need not be reevaluated. Recheck server
            # time after ownership of the row lock is actually acquired.
            current = conn.execute("SELECT clock_timestamp()").fetchone()
            if current is None or owned[0] <= current[0]:
                raise DataFailure("conflict")
            decision = PostgresSchedules._dispatch((owned[1], owned[2]))
            if (
                not isinstance(decision.dispatch, MarketDispatchInput)
                or decision.dispatch.request != request
                or decision.schedule_id != draft.schedule_id
                or decision.revision != draft.revision
                or decision.due_at != self.lease.record.next_due_at
            ):
                raise DataFailure("conflict")
            # The schedule lock lives until the outer ordinary job transaction
            # commits: an acknowledged disable cannot precede a later insert.
            return super().submit(job_id, request, parts, definitions, names)
