"""Tenant-scoped task journal, fenced leases, checkpoints and persisted throttling."""

import base64
import binascii
import json
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from qtrade_domain.authorization import Permission
from qtrade_domain.collection_coverage import matches
from qtrade_domain.ingestion import (
    CollectionRequest,
    IngestionLease,
    IngestionTask,
    ProviderPage,
    StagedPage,
    Status,
    utc,
)
from qtrade_domain.ingestion import (
    checkpoint as checkpoint_task,
)
from qtrade_domain.ingestion import (
    finish as finish_task,
)
from qtrade_domain.ingestion import (
    schedule as schedule_task,
)
from qtrade_domain.ingestion import (
    stage as stage_task,
)
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.workspaces import PostgresWorkspaceStore


class PostgresIngestionRepository(PostgresStore):
    @staticmethod
    def _time(value: object) -> datetime:
        if isinstance(value, datetime):
            return utc(value)
        if not isinstance(value, str):
            raise ValueError("invalid persisted time")
        return utc(datetime.fromisoformat(value))

    @staticmethod
    def _uuid(value: object) -> UUID:
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            return UUID(value)
        raise ValueError("invalid persisted identifier")

    @staticmethod
    def _request_payload(request: CollectionRequest) -> dict[str, object]:
        return {
            "workspace_id": str(request.workspace_id),
            "task_id": str(request.task_id),
            "source": request.source,
            "authorization_id": request.authorization_id,
            "instrument_id": request.instrument_id,
            "granularity": request.granularity,
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "created_at": request.created_at.isoformat(),
            "transformation_version": request.transformation_version,
            "page_size": request.page_size,
            "download_group_id": str(request.download_group_id)
            if request.download_group_id
            else None,
            "part_index": request.part_index,
            "part_count": request.part_count,
        }

    @staticmethod
    def _pending_payload(pending: StagedPage | None) -> dict[str, object] | None:
        if pending is None:
            return None
        return {
            "collected_at": pending.collected_at.isoformat(),
            "next_cursor": pending.page.next_cursor,
            "continuation_repeated": pending.continuation_repeated,
            "records": [
                {
                    "sequence": item.sequence,
                    "payload": base64.b64encode(item.payload).decode("ascii"),
                    "content_type": item.content_type,
                }
                for item in pending.page.records
            ],
        }

    @classmethod
    def _decode(cls, row: tuple[object, ...]) -> IngestionTask:
        try:
            (
                request_raw,
                actor_id,
                status,
                next_attempt_at,
                cursor,
                batch_ids,
                pending_raw,
                collection_complete,
                failures,
                error_code,
                dataset_id,
                seen_cursor_hashes,
            ) = row
            if not isinstance(request_raw, dict) or not isinstance(actor_id, UUID):
                raise ValueError("invalid ingestion identity")
            request_values = dict(request_raw)
            for field in ("workspace_id", "task_id"):
                request_values[field] = cls._uuid(request_values[field])
            if request_values.get("download_group_id") is not None:
                request_values["download_group_id"] = cls._uuid(request_values["download_group_id"])
            for field in ("start", "end", "created_at"):
                request_values[field] = cls._time(request_values[field])
            request = CollectionRequest(**request_values)
            pending: StagedPage | None = None
            if pending_raw is not None:
                if not isinstance(pending_raw, dict) or not isinstance(
                    pending_raw.get("records"), list
                ):
                    raise ValueError("invalid staged page")
                records = tuple(
                    RawRecord(
                        item["sequence"],
                        base64.b64decode(item["payload"], validate=True),
                        item["content_type"],
                    )
                    for item in pending_raw["records"]
                    if isinstance(item, dict)
                )
                if len(records) != len(pending_raw["records"]):
                    raise ValueError("invalid staged records")
                pending = StagedPage(
                    ProviderPage(records, cast(str | None, pending_raw["next_cursor"])),
                    cls._time(pending_raw["collected_at"]),
                    pending_raw["continuation_repeated"],
                )
            if not isinstance(batch_ids, list | tuple) or any(
                not isinstance(item, UUID) for item in batch_ids
            ):
                raise ValueError("invalid persisted checkpoints")
            if (
                not isinstance(status, str)
                or status not in {"queued", "running", "waiting", "failed", "completed"}
                or not isinstance(next_attempt_at, datetime)
                or (cursor is not None and not isinstance(cursor, str))
                or type(collection_complete) is not bool
                or type(failures) is not int
                or (error_code is not None and not isinstance(error_code, str))
                or (dataset_id is not None and not isinstance(dataset_id, UUID))
                or not isinstance(seen_cursor_hashes, list | tuple)
                or any(not isinstance(item, str) for item in seen_cursor_hashes)
            ):
                raise ValueError("invalid ingestion scheduling state")
            return IngestionTask(
                request=request,
                actor_id=actor_id,
                status=cast(Status, status),
                next_attempt_at=next_attempt_at,
                cursor=cursor,
                batch_ids=tuple(batch_ids),
                pending=pending,
                collection_complete=collection_complete,
                failures=failures,
                last_error_code=ErrorCode(error_code) if error_code is not None else None,
                dataset_id=dataset_id,
                seen_cursor_hashes=tuple(seen_cursor_hashes),
            )
        except (ValueError, TypeError, KeyError, binascii.Error, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored ingestion state") from None

    @classmethod
    def _select(
        cls,
        conn: psycopg.Connection[tuple[object, ...]],
        workspace_id: UUID,
        task_id: UUID,
        *,
        lock: bool = False,
    ) -> IngestionTask:
        suffix = sql.SQL(" FOR UPDATE") if lock else sql.SQL("")
        row = conn.execute(
            sql.SQL("""SELECT request,created_by,status,next_attempt_at,provider_cursor,batch_ids,
            pending_page,collection_complete,failures,last_error_code,dataset_id,seen_cursor_hashes
            FROM ingestion_task WHERE workspace_id=%s AND task_id=%s{}""").format(suffix),
            (workspace_id, task_id),
        ).fetchone()
        if row is None:
            raise PortError(ErrorCode.NOT_FOUND, "collection task unavailable")
        task = cls._decode(row)
        if task.request.workspace_id != workspace_id or task.request.task_id != task_id:
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored ingestion scope")
        return task

    @staticmethod
    def _authorize(
        conn: psycopg.Connection[tuple[object, ...]], actor_id: UUID, workspace_id: UUID
    ) -> None:
        conn.execute(
            "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE", (workspace_id,)
        )
        access = PostgresWorkspaceStore._access(conn, actor_id, workspace_id)
        if access is None:
            raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
        access.require(Permission.DATA_MANAGE)

    @classmethod
    def _save(
        cls,
        conn: psycopg.Connection[tuple[object, ...]],
        task: IngestionTask,
        *,
        clear_lease: bool = False,
    ) -> None:
        pending = cls._pending_payload(task.pending)
        lease_sql = sql.SQL(
            ",lease_token=NULL,lease_actor=NULL,lease_worker=NULL,lease_expires_at=NULL"
            if clear_lease
            else ""
        )
        conn.execute(
            sql.SQL("""UPDATE ingestion_task SET status=%s,next_attempt_at=%s,provider_cursor=%s,
            batch_ids=%s::uuid[],pending_page=%s::jsonb,collection_complete=%s,failures=%s,
            last_error_code=%s,dataset_id=%s,seen_cursor_hashes=%s,updated_at=now(){}
            WHERE workspace_id=%s AND task_id=%s""").format(lease_sql),
            (
                task.status,
                task.next_attempt_at,
                task.cursor,
                [str(item) for item in task.batch_ids],
                json.dumps(pending, separators=(",", ":")) if pending is not None else None,
                task.collection_complete,
                task.failures,
                task.last_error_code.value if task.last_error_code else None,
                task.dataset_id,
                list(task.seen_cursor_hashes),
                task.request.workspace_id,
                task.request.task_id,
            ),
        )

    def create(
        self, actor_id: UUID, request: CollectionRequest, *, avoid_overlap: bool = False
    ) -> IngestionTask:
        return self.create_many(actor_id, (request,), avoid_overlap=avoid_overlap)[0]

    def create_many(
        self,
        actor_id: UUID,
        requests: tuple[CollectionRequest, ...],
        *,
        avoid_overlap: bool = False,
    ) -> tuple[IngestionTask, ...]:
        if (
            not requests
            or len({item.workspace_id for item in requests}) != 1
            or len({item.task_id for item in requests}) != len(requests)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid collection task group")
        with self._connection() as conn:
            return tuple(
                self._create(conn, actor_id, item, avoid_overlap=avoid_overlap) for item in requests
            )

    def _create(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        actor_id: UUID,
        request: CollectionRequest,
        *,
        avoid_overlap: bool = False,
    ) -> IngestionTask:
        self._authorize(conn, actor_id, request.workspace_id)
        existing = conn.execute(
            "SELECT 1 FROM ingestion_task WHERE workspace_id=%s AND task_id=%s",
            (request.workspace_id, request.task_id),
        ).fetchone()
        if existing is not None:
            task = self._select(conn, request.workspace_id, request.task_id)
            if task.request != request or task.actor_id != actor_id:
                raise PortError(ErrorCode.CONFLICT, "collection task identity already used")
            return task
        if avoid_overlap:
            rows = conn.execute(
                """SELECT task_id FROM ingestion_task WHERE workspace_id=%s
                AND request->>'source'=%s AND (request->>'start')::timestamptz < %s
                AND (request->>'end')::timestamptz > %s""",
                (request.workspace_id, request.source, request.end, request.start),
            ).fetchall()
            if any(
                (item := self._select(conn, request.workspace_id, self._uuid(row[0]))).status
                != "failed"
                and matches(request, item)
                for row in rows
            ):
                raise PortError(ErrorCode.CONFLICT, "collection coverage changed; recheck")
        conn.execute(
            """INSERT INTO ingestion_task(
                workspace_id,task_id,created_by,request,status,next_attempt_at
            ) VALUES (%s,%s,%s,%s::jsonb,'queued',%s)""",
            (
                request.workspace_id,
                request.task_id,
                actor_id,
                json.dumps(self._request_payload(request), separators=(",", ":")),
                request.created_at,
            ),
        )
        return self._select(conn, request.workspace_id, request.task_id)

    def get(self, workspace_id: UUID, task_id: UUID) -> IngestionTask:
        with self._connection() as conn:
            return self._select(conn, workspace_id, task_id)

    def list_tasks(self, workspace_id: UUID, limit: int = 100) -> tuple[IngestionTask, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid collection task page size")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT request,created_by,status,next_attempt_at,provider_cursor,batch_ids,
                pending_page,collection_complete,failures,last_error_code,dataset_id,seen_cursor_hashes
                FROM ingestion_task WHERE workspace_id=%s
                ORDER BY updated_at DESC,task_id LIMIT %s""",
                (workspace_id, limit),
            ).fetchall()
        result = tuple(self._decode(row) for row in rows)
        if any(task.request.workspace_id != workspace_id for task in result):
            raise PortError(ErrorCode.UNAVAILABLE, "collection task list scope mismatch")
        return result

    def overlapping_tasks(self, request: CollectionRequest) -> tuple[IngestionTask, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT request,created_by,status,next_attempt_at,provider_cursor,batch_ids,
                pending_page,collection_complete,failures,last_error_code,dataset_id,seen_cursor_hashes
                FROM ingestion_task WHERE workspace_id=%s AND request->>'source'=%s
                AND (request->>'start')::timestamptz < %s
                AND (request->>'end')::timestamptz > %s""",
                (request.workspace_id, request.source, request.end, request.start),
            ).fetchall()
        return tuple(task for row in rows if matches(request, task := self._decode(row)))

    def ready_tasks(self, now: datetime, limit: int = 100) -> tuple[IngestionTask, ...]:
        """Return due tasks for the trusted worker scheduler across workspace scopes."""
        now = utc(now)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid collection task page size")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT t.request,t.created_by,t.status,t.next_attempt_at,t.provider_cursor,
                t.batch_ids,t.pending_page,t.collection_complete,t.failures,t.last_error_code,
                t.dataset_id,t.seen_cursor_hashes FROM ingestion_task t
                JOIN membership m ON m.workspace_id=t.workspace_id AND m.user_id=t.created_by
                JOIN qtrade_user u ON u.user_id=t.created_by
                JOIN workspace w ON w.workspace_id=t.workspace_id
                WHERE t.status IN ('queued','waiting','running') AND t.next_attempt_at<=%s
                AND (t.lease_expires_at IS NULL OR t.lease_expires_at<=%s)
                AND m.active AND m.role='admin' AND NOT u.disabled AND NOT w.archived
                ORDER BY t.next_attempt_at,t.updated_at,t.task_id LIMIT %s""",
                (now, now, limit),
            ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def requeue(
        self, actor_id: UUID, workspace_id: UUID, task_id: UUID, now: datetime
    ) -> IngestionTask:
        now = utc(now)
        with self._connection() as conn:
            self._authorize(conn, actor_id, workspace_id)
            task = self._select(conn, workspace_id, task_id, lock=True)
            if task.status == "completed":
                return task
            if task.status != "failed":
                raise PortError(ErrorCode.CONFLICT, "only failed collection tasks can be rerun")
            result = IngestionTask(
                task.request,
                task.actor_id,
                "queued",
                now,
                task.cursor,
                task.batch_ids,
                task.pending,
                task.collection_complete,
                seen_cursor_hashes=task.seen_cursor_hashes,
            )
            self._save(conn, result, clear_lease=True)
            return result

    def claim(
        self,
        actor_id: UUID,
        worker_id: UUID,
        workspace_id: UUID,
        task_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> IngestionLease | None:
        now, expires_at = utc(now), utc(expires_at)
        if not isinstance(worker_id, UUID) or expires_at <= now:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid ingestion lease request")
        with self._connection() as conn:
            self._authorize(conn, actor_id, workspace_id)
            task = self._select(conn, workspace_id, task_id, lock=True)
            if task.status not in {"queued", "waiting", "running"} or task.next_attempt_at > now:
                return None
            row = conn.execute(
                """SELECT lease_token,lease_actor,lease_worker,lease_expires_at
                FROM ingestion_task WHERE workspace_id=%s AND task_id=%s""",
                (workspace_id, task_id),
            ).fetchone()
            if row is None:
                raise PortError(ErrorCode.NOT_FOUND, "collection task unavailable")
            if row[0] is not None and isinstance(row[3], datetime) and row[3] > now:
                return None
            token = uuid4()
            conn.execute(
                """UPDATE ingestion_task SET status='running',lease_token=%s,lease_actor=%s,
                lease_worker=%s,lease_expires_at=%s,updated_at=now()
                WHERE workspace_id=%s AND task_id=%s""",
                (token, actor_id, worker_id, expires_at, workspace_id, task_id),
            )
            task = IngestionTask(
                task.request,
                task.actor_id,
                "running",
                task.next_attempt_at,
                task.cursor,
                task.batch_ids,
                task.pending,
                task.collection_complete,
                task.failures,
                task.last_error_code,
                seen_cursor_hashes=task.seen_cursor_hashes,
            )
            return IngestionLease(task, token, actor_id, worker_id, expires_at)

    def _locked(
        self, conn: psycopg.Connection[tuple[object, ...]], lease: IngestionLease, now: datetime
    ) -> IngestionTask:
        self._authorize(conn, lease.actor_id, lease.task.request.workspace_id)
        task = self._select(
            conn, lease.task.request.workspace_id, lease.task.request.task_id, lock=True
        )
        row = conn.execute(
            """SELECT lease_token,lease_actor,lease_worker,lease_expires_at
            FROM ingestion_task WHERE workspace_id=%s AND task_id=%s""",
            (task.request.workspace_id, task.request.task_id),
        ).fetchone()
        if (
            task.status != "running"
            or row is None
            or row[0] != lease.token
            or row[1] != lease.actor_id
            or row[2] != lease.worker_id
            or not isinstance(row[3], datetime)
            or row[3] != lease.expires_at
            or row[3] <= utc(now)
        ):
            raise PortError(ErrorCode.CONFLICT, "collection lease expired")
        return task

    def stage(self, lease: IngestionLease, page: StagedPage, now: datetime) -> IngestionLease:
        with self._connection() as conn:
            task = self._locked(conn, lease, now)
            result = stage_task(task, page)
            self._save(conn, result)
            return IngestionLease(
                result, lease.token, lease.actor_id, lease.worker_id, lease.expires_at
            )

    def checkpoint(self, lease: IngestionLease, batch_id: UUID, now: datetime) -> IngestionTask:
        with self._connection() as conn:
            task = self._locked(conn, lease, now)
            result = checkpoint_task(task, batch_id, now)
            self._save(conn, result, clear_lease=True)
            return result

    def schedule(
        self,
        lease: IngestionLease,
        code: ErrorCode,
        deadline: datetime,
        now: datetime,
        *,
        terminal: bool,
        count_failure: bool = True,
    ) -> IngestionTask:
        with self._connection() as conn:
            task = self._locked(conn, lease, now)
            result = schedule_task(
                task, code, deadline, terminal=terminal, count_failure=count_failure
            )
            self._save(conn, result, clear_lease=True)
            return result

    def finish(
        self, lease: IngestionLease, dataset_id: UUID | None, now: datetime
    ) -> IngestionTask:
        with self._connection() as conn:
            task = self._locked(conn, lease, now)
            result = finish_task(task, dataset_id, now)
            self._save(conn, result, clear_lease=True)
            return result

    def reserve_rate_slot(
        self, request: CollectionRequest, now: datetime, interval: timedelta
    ) -> datetime:
        now = utc(now)
        if interval <= timedelta(0):
            raise PortError(ErrorCode.INVALID_INPUT, "provider interval must be positive")
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO ingestion_source_rate(
                    workspace_id,source,authorization_id,next_allowed_at
                ) VALUES (%s,%s,%s,%s)
                ON CONFLICT(workspace_id,source,authorization_id) DO NOTHING""",
                (request.workspace_id, request.source, request.authorization_id, now),
            )
            row = conn.execute(
                """SELECT next_allowed_at FROM ingestion_source_rate
                WHERE workspace_id=%s AND source=%s AND authorization_id=%s FOR UPDATE""",
                (request.workspace_id, request.source, request.authorization_id),
            ).fetchone()
            if row is None:
                raise PortError(ErrorCode.UNAVAILABLE, "provider throttle unavailable")
            if not isinstance(row[0], datetime):
                raise PortError(ErrorCode.UNAVAILABLE, "invalid provider throttle state")
            deadline = row[0]
            if deadline > now:
                return deadline
            conn.execute(
                """UPDATE ingestion_source_rate SET next_allowed_at=%s
                WHERE workspace_id=%s AND source=%s AND authorization_id=%s""",
                (now + interval, request.workspace_id, request.source, request.authorization_id),
            )
            return now

    def defer_source(self, request: CollectionRequest, deadline: datetime) -> None:
        deadline = utc(deadline)
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO ingestion_source_rate(
                    workspace_id,source,authorization_id,next_allowed_at
                ) VALUES (%s,%s,%s,%s)
                ON CONFLICT(workspace_id,source,authorization_id)
                DO UPDATE SET next_allowed_at=GREATEST(ingestion_source_rate.next_allowed_at,
                    EXCLUDED.next_allowed_at)""",
                (request.workspace_id, request.source, request.authorization_id, deadline),
            )
