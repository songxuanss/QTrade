"""In-memory implementation of the durable alert evaluation queue contract."""

from datetime import datetime, timedelta
from threading import RLock
from uuid import UUID, uuid4

from qtrade_domain.alert_jobs import AlertEvaluationJob, AlertEvaluationLease
from qtrade_domain.ports import ErrorCode, PortError


class MockAlertEvaluationQueue:
    def __init__(self) -> None:
        self._jobs: dict[UUID, tuple[AlertEvaluationJob, str, int, datetime, UUID | None]] = {}
        self._lock = RLock()

    def enqueue(self, job: AlertEvaluationJob) -> AlertEvaluationJob:
        with self._lock:
            existing = self._jobs.get(job.job_id)
            if existing is not None and existing[0] != job:
                raise PortError(ErrorCode.CONFLICT, "alert evaluation id reused")
            self._jobs[job.job_id] = (
                job,
                "queued",
                0,
                datetime.min.replace(tzinfo=job.occurred_at.tzinfo),
                None,
            )
        return job

    def ready(
        self, now: datetime, limit: int, lease_seconds: int
    ) -> tuple[AlertEvaluationLease, ...]:
        if now.tzinfo is None or type(limit) is not int or not 1 <= limit <= 100:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert queue poll")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 300:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert queue lease")
        leases: list[AlertEvaluationLease] = []
        with self._lock:
            for job_id, (job, status, attempts, available, _expiry) in tuple(self._jobs.items()):
                if status == "completed" or status == "failed" or available > now:
                    continue
                token = uuid4()
                attempts += 1
                self._jobs[job_id] = (
                    job,
                    "running",
                    attempts,
                    now + timedelta(seconds=lease_seconds),
                    token,
                )
                leases.append(AlertEvaluationLease(job, token, attempts))
                if len(leases) >= limit:
                    break
        return tuple(leases)

    def finish(
        self,
        lease: AlertEvaluationLease,
        now: datetime,
        *,
        succeeded: bool,
        max_attempts: int = 8,
    ) -> bool:
        with self._lock:
            entry = self._jobs.get(lease.job.job_id)
            if entry is None or entry[1] != "running" or entry[4] != lease.token:
                return False
            job, _, attempts, expiry, token = entry
            if succeeded:
                status, available = "completed", now
            elif attempts >= max_attempts:
                status, available = "failed", now
            else:
                status, available = "queued", now + timedelta(seconds=min(3600, 2**attempts))
            self._jobs[lease.job.job_id] = (job, status, attempts, available, None)
            return True
