"""Independent scoped task storage, transactional plans and fenced expiring leases."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from time import sleep
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from qtrade_data_contracts.collection import (
    CollectionJobRequest,
    CollectionPublication,
    FactorDefinition,
)
from qtrade_data_contracts.collection_replay import CollectionReplayQueueRequest
from qtrade_data_contracts.freshness import FreshnessKey
from qtrade_data_core.collection import CollectionPart, JobRepository
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.usage import DenialReason
from qtrade_data_postgres.dispatch import FairDispatch


class PostgresJobs:
    def __init__(self, database_url: str, conn: psycopg.Connection[Any] | None = None):
        self.url, self.conn = database_url, conn

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            if self.conn is not None:
                yield self.conn
            else:
                with psycopg.connect(self.url) as conn:
                    yield conn
        except psycopg.Error:
            raise DataFailure("unavailable") from None

    @contextmanager
    def atomic(self, scope_id: UUID) -> Iterator[JobRepository]:
        with self._connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(scope_id),))
            yield PostgresJobs(self.url, conn)

    @staticmethod
    def _part(row: tuple[Any, ...]) -> CollectionPart:
        body = row[0]
        execution_kind = body.get("execution_kind", "collection")
        if execution_kind not in {"collection", "normalization_replay"}:
            raise DataFailure("unverified")
        try:
            request = (
                CollectionReplayQueueRequest.model_validate_json(
                    json.dumps(body["request"])
                ).request
                if execution_kind == "normalization_replay"
                else CollectionJobRequest.model_validate_json(json.dumps(body["request"]))
            )
        except ValueError:
            raise DataFailure("unverified") from None
        return CollectionPart(
            UUID(body["part_id"]),
            UUID(body["job_id"]),
            request,
            body["instrument_id"],
            body["field"],
            datetime.fromisoformat(body["start"]),
            datetime.fromisoformat(body["end"]),
            body["mapping_version"],
            body.get("instrument_name", "名称未核验"),
            row[1],
            row[2],
            row[3],
            execution_kind,
        )

    def part_for_job(self, scope_id: UUID, job_id: UUID, part_id: UUID) -> CollectionPart:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT p.payload,p.state,p.attempt,p.lease_token
                FROM qtrade_data.collection_part p
                JOIN qtrade_data.collection_job_part l USING(scope_id,part_id)
                WHERE l.scope_id=%s AND l.job_id=%s AND l.part_id=%s""",
                (scope_id, job_id, part_id),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            return self._part(row)

    def find(self, scope_id: UUID, request_id: UUID) -> dict[str, object] | None:
        try:
            return self.job(scope_id, request_id)
        except DataFailure as error:
            if error.code == "not_found":
                return None
            raise

    def coverage(self, part: CollectionPart) -> tuple[CollectionPart, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT payload,state,attempt,lease_token FROM qtrade_data.collection_part
                WHERE scope_id=%s AND coverage_key=%s AND start_at<%s AND end_at>%s
                AND state IN ('completed','queued','running','waiting') ORDER BY
                start_at,part_id""",
                (part.request.scope_id, part.coverage_key, part.end, part.start),
            ).fetchall()
        return tuple(self._part(row) for row in rows)

    def submit(
        self,
        job_id: UUID,
        request: CollectionJobRequest,
        parts: tuple[CollectionPart, ...],
        definitions: tuple[FactorDefinition, ...],
        names: dict[str, str],
    ) -> dict[str, object]:
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO
                qtrade_data.collection_job(scope_id,job_id,actor_id,request,field_definitions,instrument_names)
                VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)""",
                (
                    request.scope_id,
                    job_id,
                    request.actor_id,
                    request.model_dump_json(),
                    json.dumps([item.model_dump(mode="json") for item in definitions]),
                    json.dumps(names),
                ),
            )
            for ordinal, part in enumerate(parts):
                body = {
                    "part_id": str(part.part_id),
                    "job_id": str(part.job_id),
                    "request": (
                        CollectionReplayQueueRequest(
                            source=part.request.source, request=part.request
                        ).model_dump(mode="json")
                        if part.execution_kind == "normalization_replay"
                        else part.request.model_dump(mode="json")
                    ),
                    "instrument_id": part.instrument_id,
                    "field": part.field,
                    "start": part.start.isoformat(),
                    "end": part.end.isoformat(),
                    "mapping_version": part.mapping_version,
                    "instrument_name": part.instrument_name,
                    "execution_kind": part.execution_kind,
                }
                conn.execute(
                    """INSERT INTO qtrade_data.collection_part
                    (scope_id,part_id,job_id,coverage_key,start_at,end_at,payload)
                    VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                    (
                        request.scope_id,
                        part.part_id,
                        part.job_id,
                        part.coverage_key,
                        part.start,
                        part.end,
                        json.dumps(body),
                    ),
                )
                conn.execute(
                    "INSERT INTO qtrade_data.collection_job_part VALUES(%s,%s,%s,%s)",
                    (request.scope_id, job_id, part.part_id, ordinal),
                )
            return PostgresJobs(self.url, conn).job(request.scope_id, job_id)

    def job(self, scope_id: UUID, job_id: UUID, offset: int = 0) -> dict[str, object]:
        with self._connection() as conn:
            head = conn.execute(
                """SELECT request,created_at,cancelled,field_definitions,instrument_names FROM
                qtrade_data.collection_job WHERE scope_id=%s AND job_id=%s""",
                (scope_id, job_id),
            ).fetchone()
            if head is None:
                raise DataFailure("not_found")
            rows = conn.execute(
                """SELECT p.state,count(*) FROM qtrade_data.collection_part p
                JOIN qtrade_data.collection_job_part l USING(scope_id,part_id)
                WHERE l.scope_id=%s AND l.job_id=%s GROUP BY p.state""",
                (scope_id, job_id),
            ).fetchall()
            counts = {str(row[0]): int(row[1]) for row in rows}
            rows = conn.execute(
                """SELECT p.part_id,p.payload,p.state,p.attempt,p.error_code,
                p.next_attempt_at,p.publication,p.wait_reason FROM qtrade_data.collection_part p
                JOIN qtrade_data.collection_job_part l USING(scope_id,part_id)
                WHERE l.scope_id=%s AND l.job_id=%s ORDER BY l.ordinal LIMIT 100 OFFSET %s""",
                (scope_id, job_id, offset),
            ).fetchall()
            total = sum(counts.values())
            state = (
                "cancelled"
                if head[2]
                else "waiting"
                if total > 0 and counts.get("waiting", 0) == total
                else "running"
                if any(counts.get(s, 0) for s in ("queued", "waiting", "running"))
                else "failed"
                if counts.get("failed")
                else "no_data"
                if counts.get("no_data") == total and total > 0
                else "completed_with_gaps"
                if counts.get("no_data")
                else "completed"
            )
            return {
                "schema_version": 1,
                "scope_id": str(scope_id),
                "job_id": str(job_id),
                "request": head[0],
                "created_at": head[1].isoformat(),
                "state": state,
                "field_definitions": head[3],
                "instrument_names": head[4],
                "counts": counts,
                "total_parts": total,
                "offset": offset,
                "next_offset": offset + len(rows) if offset + len(rows) < total else None,
                "parts": [
                    {
                        "part_id": str(r[0]),
                        "instrument_id": r[1]["instrument_id"],
                        "instrument_name": r[1].get("instrument_name", "名称未核验"),
                        "field": r[1]["field"],
                        "start": r[1]["start"],
                        "end": r[1]["end"],
                        "execution_kind": r[1].get("execution_kind", "collection"),
                        "state": r[2],
                        "attempt": r[3],
                        "error_code": r[4],
                        "next_attempt_at": r[5].isoformat(),
                        "publication": r[6],
                        "wait_reason": r[7],
                    }
                    for r in rows
                ],
            }

    def jobs(self, scope_id: UUID) -> list[dict[str, object]]:
        with self._connection() as conn:
            ids = conn.execute(
                """SELECT job_id FROM qtrade_data.collection_job WHERE scope_id=%s ORDER BY
                created_at DESC LIMIT 20""",
                (scope_id,),
            ).fetchall()
            return [PostgresJobs(self.url, conn).job(scope_id, row[0]) for row in ids]

    def claim(self, now: datetime) -> CollectionPart | None:
        with self._connection() as conn:
            dispatch = FairDispatch(conn, "collection")
            chosen = dispatch.choose(
                sql.SQL(
                    """SELECT p.scope_id,p.part_id AS work_id,p.job_id AS run_id,
                p.payload->'request'->>'source' AS source,next_attempt_at AS due_at
                FROM qtrade_data.collection_part p
                WHERE ((state IN ('queued','waiting') AND next_attempt_at<=%s)
                  OR (state='running' AND lease_until<%s))"""
                ),
                (now, now),
            )
            if chosen is None:
                return None
            lease = uuid4()
            result = conn.execute(
                """UPDATE qtrade_data.collection_part SET state='running',attempt=attempt+1,
                wait_reason=NULL,
                lease_token=%s,lease_until=%s,updated_at=%s WHERE scope_id=%s AND part_id=%s
                AND ((state IN ('queued','waiting') AND next_attempt_at<=%s)
                  OR (state='running' AND lease_until<%s))
                RETURNING payload,state,attempt,lease_token""",
                (
                    lease,
                    now + timedelta(minutes=10),
                    now,
                    chosen.scope_id,
                    chosen.work_id,
                    now,
                    now,
                ),
            ).fetchone()
            if result:
                dispatch.advance(chosen)
            return self._part(result) if result else None

    def complete(self, part: CollectionPart, publication: CollectionPublication | None) -> None:
        with self._connection() as conn:
            result = conn.execute(
                """UPDATE qtrade_data.collection_part SET state=%s,publication=%s::jsonb,
                error_code=NULL,wait_reason=NULL,lease_token=NULL,lease_until=NULL,updated_at=now()
                WHERE scope_id=%s AND part_id=%s AND lease_token=%s AND state='running'
                AND (payload->>'execution_kind' IS DISTINCT FROM 'normalization_replay'
                     OR lease_until>clock_timestamp())""",
                (
                    "completed" if publication else "no_data",
                    publication.model_dump_json() if publication else None,
                    part.request.scope_id,
                    part.part_id,
                    part.lease_token,
                ),
            )
            if result.rowcount != 1:
                raise DataFailure("conflict")

    def fail(self, part: CollectionPart, code: str, retry_at: datetime | None) -> None:
        with self._connection() as conn:
            conn.execute(
                """UPDATE qtrade_data.collection_part SET state=%s,error_code=%s,next_attempt_at=%s,
                wait_reason=NULL,lease_token=NULL,lease_until=NULL,updated_at=now()
                WHERE scope_id=%s AND part_id=%s AND lease_token=%s AND state='running'
                AND (payload->>'execution_kind' IS DISTINCT FROM 'normalization_replay'
                     OR lease_until>clock_timestamp())""",
                (
                    "waiting" if retry_at else "failed",
                    code,
                    retry_at or datetime.now(UTC),
                    part.request.scope_id,
                    part.part_id,
                    part.lease_token,
                ),
            )

    def wait_budget(
        self, part: CollectionPart, reason: DenialReason, retry_at: datetime | None
    ) -> None:
        if retry_at is not None and retry_at.utcoffset() is None:
            raise ValueError("explicit budget retry clock required")
        with self._connection() as conn:
            # The denied admission did not fail a supplier request. Return its
            # claim attempt to the failure allowance; never refund HTTP credits.
            result = conn.execute(
                "UPDATE qtrade_data.collection_part SET state=%s,wait_reason=%s,"
                "error_code='rate_limited',next_attempt_at=%s,attempt=greatest(attempt-1,0),"
                "lease_token=NULL,lease_until=NULL,updated_at=now() WHERE scope_id=%s AND "
                "part_id=%s AND state='running' AND lease_token=%s",
                (
                    "waiting" if retry_at else "failed",
                    reason,
                    retry_at or datetime.now(UTC),
                    part.request.scope_id,
                    part.part_id,
                    part.lease_token,
                ),
            )
            if result.rowcount != 1:
                raise DataFailure("conflict")

    def control(self, scope_id: UUID, job_id: UUID, action: str) -> None:
        with self.atomic(scope_id) as repo:
            store = cast(PostgresJobs, repo)
            store.job(scope_id, job_id)
            with store._connection() as conn:
                if action == "cancel":
                    conn.execute(
                        """UPDATE qtrade_data.collection_job SET cancelled=true WHERE scope_id=%s
                        AND job_id=%s""",
                        (scope_id, job_id),
                    )
                    # Referenced work continues for other active jobs; cancel only unshared parts.
                    conn.execute(
                        """UPDATE qtrade_data.collection_part p SET
                        state='cancelled',wait_reason=NULL,lease_token=NULL,lease_until=NULL
                        WHERE p.scope_id=%s AND p.job_id=%s AND state IN
                        ('queued','waiting','running')
                        AND NOT EXISTS(SELECT 1 FROM qtrade_data.collection_job_part l
                        JOIN qtrade_data.collection_job j USING(scope_id,job_id)
                        WHERE l.scope_id=p.scope_id AND l.part_id=p.part_id AND NOT j.cancelled)""",
                        (scope_id, job_id),
                    )
                elif action == "retry":
                    conn.execute(
                        "UPDATE qtrade_data.collection_job SET cancelled=false WHERE "
                        "scope_id=%s AND job_id=%s",
                        (scope_id, job_id),
                    )
                    conn.execute(
                        """UPDATE qtrade_data.collection_part p SET
                        state='queued',attempt=0,error_code=NULL,wait_reason=NULL,
                        next_attempt_at=now(),lease_token=NULL,lease_until=NULL WHERE p.scope_id=%s
                        AND (state IN ('failed','no_data','cancelled') OR
                             (state='waiting' AND wait_reason IS NOT NULL)) AND EXISTS(
                        SELECT 1 FROM qtrade_data.collection_job_part l WHERE l.scope_id=p.scope_id
                        AND l.part_id=p.part_id AND l.job_id=%s)""",
                        (scope_id, job_id),
                    )

    def publication(self, scope_id: UUID, publication_id: UUID) -> CollectionPublication:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT publication FROM qtrade_data.collection_part WHERE scope_id=%s AND
                part_id=%s AND state='completed'""",
                (scope_id, publication_id),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            return CollectionPublication.model_validate_json(json.dumps(row[0]))

    def latest_publication(self, key: FreshnessKey) -> CollectionPublication:
        """Newest matching publication is a candidate, never completeness proof.

        The verifier checks exact scope/key and archives before any checkpoint;
        the freshness merger retains stronger previously verified progress.
        """
        with self._connection() as conn:
            row = conn.execute(
                """SELECT publication FROM qtrade_data.collection_part
                WHERE scope_id=%s AND state='completed'
                AND publication->>'source'=%s AND publication->>'instrument_id'=%s
                AND publication->>'mapping_version'=%s
                AND publication->>'dataset_kind'='bars' AND publication->>'granularity'='1d'
                AND publication->'evidence'->>'adjustment'='none'
                ORDER BY updated_at DESC,part_id DESC LIMIT 1""",
                (key.scope_id, key.source, key.instrument_id, key.mapping_version),
            ).fetchone()
            if row is None:
                raise DataFailure("not_found")
            return CollectionPublication.model_validate_json(json.dumps(row[0]))

    def deliveries(self, consumer: str) -> list[dict[str, object]]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT p.publication FROM qtrade_data.collection_part p WHERE state='completed'
                AND publication->>'dataset_kind'='bars' AND NOT EXISTS(
                SELECT 1 FROM qtrade_data.collection_delivery d WHERE d.scope_id=p.scope_id
                AND d.publication_id=p.part_id AND d.consumer_id=%s) ORDER BY p.updated_at
                LIMIT 50""",
                (consumer,),
            ).fetchall()
            return [cast(dict[str, object], row[0]) for row in rows]

    def acknowledge(self, scope_id: UUID, publication_id: UUID, consumer: str) -> None:
        self.publication(scope_id, publication_id)
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO
                qtrade_data.collection_delivery(scope_id,publication_id,consumer_id)
                VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""",
                (scope_id, publication_id, consumer),
            )

    def pace(self, source: str, interval: float) -> None:
        now = datetime.now(UTC)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO qtrade_data.source_clock VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (source, now),
            )
            row = conn.execute(
                "SELECT next_at FROM qtrade_data.source_clock WHERE source=%s FOR UPDATE", (source,)
            ).fetchone()
            if row is None:
                raise DataFailure("unavailable")
            at = max(now, row[0])
            conn.execute(
                "UPDATE qtrade_data.source_clock SET next_at=%s WHERE source=%s",
                (at + timedelta(seconds=interval), source),
            )
        delay = (at - datetime.now(UTC)).total_seconds()
        if delay > 0:
            while delay > 0:
                sleep(min(delay, 30))
                delay = (at - datetime.now(UTC)).total_seconds()
