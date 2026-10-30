"""Durable, leased queue for alert fact evaluation."""

import json
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from qtrade_domain.alert_jobs import (
    AlertEvaluationJob,
    AlertEvaluationLease,
    AlertEvaluationQueuePort,
)
from qtrade_domain.alerts import AlertSource
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.connection import PostgresStore


class PostgresAlertEvaluationQueue(PostgresStore, AlertEvaluationQueuePort):
    @staticmethod
    def _decode(row: tuple[object, ...]) -> AlertEvaluationJob:
        try:
            job_id, workspace_id, actor_id, source, facts, occurred_at, resource_id, context = row
            if not isinstance(facts, dict) or not isinstance(context, dict):
                raise ValueError("invalid fact object")
            return AlertEvaluationJob(
                cast(UUID, job_id),
                cast(UUID, workspace_id),
                cast(UUID, actor_id),
                AlertSource(cast(str, source)),
                tuple(sorted(facts.items())),
                cast(datetime, occurred_at),
                cast(str | None, resource_id),
                json.dumps(context, separators=(",", ":"), allow_nan=False),
            )
        except (ValueError, TypeError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored alert evaluation job") from None

    def enqueue(self, job: AlertEvaluationJob) -> AlertEvaluationJob:
        now = datetime.now(UTC)
        facts = json.dumps(dict(job.facts), separators=(",", ":"), allow_nan=False)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO alert_evaluation_job "
                "(job_id,workspace_id,actor_id,source,facts,occurred_at,resource_id,status,"
                "context_snapshot,available_at,created_at,updated_at) "
                "VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s,'queued',%s::jsonb,%s,%s,%s)",
                (
                    job.job_id,
                    job.workspace_id,
                    job.actor_id,
                    job.source.value,
                    facts,
                    job.occurred_at,
                    job.resource_id,
                    job.context_snapshot_json,
                    now,
                    now,
                    now,
                ),
            )
        return job

    def ready(
        self, now: datetime, limit: int, lease_seconds: int
    ) -> tuple[AlertEvaluationLease, ...]:
        if (
            now.tzinfo is None
            or type(limit) is not int
            or not 1 <= limit <= 100
            or type(lease_seconds) is not int
            or not 5 <= lease_seconds <= 300
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert queue poll")
        leases: list[AlertEvaluationLease] = []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT job_id,workspace_id,actor_id,source,facts,occurred_at,resource_id,"
                "context_snapshot,attempts "
                "FROM alert_evaluation_job WHERE "
                "((status='queued' AND available_at<=%s) OR "
                "(status='running' AND lease_expires_at<=%s)) "
                "ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT %s",
                (now, now, limit),
            ).fetchall()
            for row in rows:
                token = uuid4()
                job = self._decode(row[:8])
                attempts = cast(int, row[8]) + 1
                conn.execute(
                    "UPDATE alert_evaluation_job SET status='running',attempts=%s,"
                    "lease_token=%s,lease_expires_at=%s,updated_at=%s WHERE job_id=%s",
                    (
                        attempts,
                        token,
                        now + timedelta(seconds=lease_seconds),
                        now,
                        job.job_id,
                    ),
                )
                leases.append(AlertEvaluationLease(job, token, attempts))
        return tuple(leases)

    def finish(
        self,
        lease: AlertEvaluationLease,
        now: datetime,
        *,
        succeeded: bool,
        max_attempts: int = 8,
    ) -> bool:
        if (
            now.tzinfo is None
            or type(succeeded) is not bool
            or type(max_attempts) is not int
            or not 1 <= max_attempts <= 20
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert queue completion")
        status = (
            "completed" if succeeded else ("failed" if lease.attempts >= max_attempts else "queued")
        )
        retry_at = now + timedelta(seconds=min(3600, 2 ** min(lease.attempts, 12)))
        with self._connection() as conn:
            result = conn.execute(
                "UPDATE alert_evaluation_job SET status=%s,available_at=%s,lease_token=NULL,"
                "lease_expires_at=NULL,updated_at=%s WHERE job_id=%s AND status='running' "
                "AND lease_token=%s",
                (
                    status,
                    now if succeeded or status == "failed" else retry_at,
                    now,
                    lease.job.job_id,
                    lease.token,
                ),
            )
            return result.rowcount == 1
