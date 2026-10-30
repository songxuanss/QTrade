"""Durable research queue with ownership fencing and bounded crash recovery."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg import sql

from qtrade_data_contracts.research_transport import ResearchPlan
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research_worker import ResearchJobLease as ResearchLease
from qtrade_data_core.usage import DenialReason
from qtrade_data_postgres.dispatch import FairDispatch
from qtrade_data_postgres.research_publications import PostgresResearchPublications


class PostgresResearchJobs(PostgresResearchPublications):
    def enqueue(self, plan: ResearchPlan) -> None:
        self.register(plan)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO qtrade_data.research_job(scope_id,request_id) VALUES(%s,%s) "
                "ON CONFLICT DO NOTHING",
                (plan.request.scope_id, plan.request.request_id),
            )

    def claim(self, *, lease_seconds: int = 600, max_attempts: int = 3) -> ResearchLease | None:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid lease duration")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("invalid attempt bound")
        with self._connection() as conn:
            conn.execute(
                "UPDATE qtrade_data.research_job SET state='failed', "
                "lease_id=NULL,lease_until=NULL,error_code='timeout',updated_at=now() "
                "WHERE state='running' AND lease_until<now() AND attempts>=%s",
                (max_attempts,),
            )
            dispatch = FairDispatch(conn, "research")
            chosen = dispatch.choose(
                sql.SQL(
                    "SELECT j.scope_id,j.request_id AS work_id,j.request_id AS run_id,"
                    "r.plan->'request'->>'source' AS source,j.created_at AS due_at "
                    "FROM qtrade_data.research_job j JOIN qtrade_data.research_run r "
                    "USING(scope_id,request_id) WHERE j.state='queued' OR "
                    "(j.state='waiting' AND j.next_attempt_at<=now()) OR "
                    "(j.state='running' AND j.lease_until<now() AND j.attempts<%s)"
                ),
                (max_attempts,),
            )
            if chosen is None:
                return None
            scope, request = chosen.scope_id, chosen.work_id
            row = conn.execute(
                "SELECT attempts FROM qtrade_data.research_job WHERE scope_id=%s AND request_id=%s",
                (scope, request),
            ).fetchone()
            if row is None:
                raise DataFailure("conflict")
            attempts = row[0]
            token = uuid4()
            updated = conn.execute(
                "UPDATE qtrade_data.research_job SET state='running',wait_reason=NULL,lease_id=%s, "
                "lease_until=now()+%s*interval '1 second',attempts=attempts+1, "
                "updated_at=now() WHERE scope_id=%s AND request_id=%s AND "
                "(state='queued' OR (state='waiting' AND next_attempt_at<=now()) OR "
                "(state='running' AND lease_until<now() AND attempts<%s))",
                (token, lease_seconds, scope, request, max_attempts),
            )
            if updated.rowcount != 1:
                return None
            dispatch.advance(chosen)
            return ResearchLease(self._plan(conn, scope, request), token, attempts + 1)

    def finish(
        self, lease: ResearchLease, *, publication: UUID | None = None, error: str | None = None
    ) -> None:
        if publication is not None and error is not None:
            raise ValueError("publication and failure are exclusive")
        if error not in {
            None,
            "unverified",
            "unauthorized",
            "forbidden",
            "unsupported",
            "unavailable",
            "timeout",
            "rate_limited",
            "conflict",
            "invalid_input",
        }:
            raise ValueError("unsafe error code")
        state = "failed" if error else "completed" if publication else "no_observations"
        with self._connection() as conn:
            # A completed publication must originate in this exact frozen run.
            if publication is not None:
                manifest = self.manifest(lease.plan.request.scope_id, publication)
                if manifest.artifact.request_id != lease.plan.request.request_id:
                    raise DataFailure("conflict")
            result = conn.execute(
                "UPDATE qtrade_data.research_job SET state=%s, publication_id=%s,error_code=%s, "
                "wait_reason=NULL,lease_id=NULL,lease_until=NULL,updated_at=now() "
                "WHERE scope_id=%s AND request_id=%s "
                "AND state='running' AND lease_id=%s AND lease_until>now()",
                (
                    state,
                    publication,
                    error,
                    lease.plan.request.scope_id,
                    lease.plan.request.request_id,
                    lease.lease_id,
                ),
            )
            if result.rowcount != 1:
                raise DataFailure("conflict")

    def wait_budget(
        self, lease: ResearchLease, reason: DenialReason, retry_at: datetime | None
    ) -> None:
        if retry_at is not None and retry_at.utcoffset() is None:
            raise ValueError("explicit budget retry clock required")
        with self._connection() as conn:
            # Fence ownership just like finish. Credit history and any raw pages
            # already acquired remain immutable; only this denied claim returns
            # to the failure allowance, never a refunded provider request.
            updated = conn.execute(
                "UPDATE qtrade_data.research_job SET state=%s,wait_reason=%s,"
                "error_code='rate_limited',next_attempt_at=%s,attempts=greatest(attempts-1,0),"
                "lease_id=NULL,lease_until=NULL,updated_at=now() WHERE scope_id=%s AND "
                "request_id=%s AND state='running' AND lease_id=%s AND lease_until>now()",
                (
                    "waiting" if retry_at else "failed",
                    reason,
                    retry_at or datetime.now(UTC),
                    lease.plan.request.scope_id,
                    lease.plan.request.request_id,
                    lease.lease_id,
                ),
            )
            if updated.rowcount != 1:
                raise DataFailure("conflict")

    def retry(self, scope: UUID, request: UUID) -> None:
        with self._connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM qtrade_data.research_job WHERE scope_id=%s AND request_id=%s",
                (scope, request),
            ).fetchone()
            if not exists:
                raise DataFailure("not_found")
            # Repeated clicks must not reset an active lease or completed
            # publication. Only a recoverable terminal/waiting state is changed;
            # frozen plan/actor and already acquired evidence remain untouched.
            conn.execute(
                "UPDATE qtrade_data.research_job SET state='queued',attempts=0,wait_reason=NULL,"
                "error_code=NULL,next_attempt_at=now(),lease_id=NULL,lease_until=NULL,"
                "updated_at=now() WHERE scope_id=%s AND request_id=%s "
                "AND state IN ('failed','waiting','no_observations') "
                "AND publication_id IS NULL",
                (scope, request),
            )

    def job(self, scope: UUID, request: UUID) -> dict[str, object]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT state,attempts,publication_id,error_code,wait_reason,next_attempt_at "
                "FROM qtrade_data.research_job WHERE scope_id=%s AND request_id=%s",
                (scope, request),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            return dict(
                state=row[0],
                attempts=row[1],
                publication_id=row[2],
                error_code=row[3],
                wait_reason=row[4],
                next_attempt_at=row[5],
            )

    def jobs(self, scope: UUID, *, limit: int = 100) -> tuple[dict[str, object], ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid list bound")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT j.request_id,j.state,j.attempts,j.publication_id,j.error_code,r.plan,"
                "j.wait_reason,j.next_attempt_at "
                "FROM qtrade_data.research_job j JOIN qtrade_data.research_run r "
                "USING(scope_id,request_id) WHERE j.scope_id=%s "
                "ORDER BY j.created_at DESC,j.request_id LIMIT %s",
                (scope, limit),
            ).fetchall()
            return tuple(
                dict(
                    request_id=row[0],
                    state=row[1],
                    attempts=row[2],
                    publication_id=row[3],
                    error_code=row[4],
                    request=row[5]["request"],
                    wait_reason=row[6],
                    next_attempt_at=row[7],
                )
                for row in rows
            )
