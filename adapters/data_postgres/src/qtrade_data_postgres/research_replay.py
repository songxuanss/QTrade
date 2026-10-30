"""Atomic offline intent/lineage/queue submission with scoped immutable reads."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg

from qtrade_data_contracts.research_archive import ResearchRawManifest
from qtrade_data_contracts.research_replay import ResearchReplay
from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.research_jobs import PostgresResearchJobs


class PostgresResearchReplays(PostgresResearchJobs):
    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            with psycopg.connect(self.url, connect_timeout=5) as conn:
                conn.execute("SET LOCAL statement_timeout='10s'")
                conn.execute("SET LOCAL lock_timeout='5s'")
                yield conn
        except psycopg.Error:
            raise DataFailure("unavailable") from None

    def raw_inputs(self, scope: UUID, request: UUID, limit: int) -> tuple[ResearchRawManifest, ...]:
        if type(limit) is not int or not 1 <= limit <= 1001:
            raise DataFailure("invalid_input")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT manifest FROM qtrade_data.research_raw_batch "
                "WHERE scope_id=%s AND request_id=%s ORDER BY batch_id LIMIT %s",
                (scope, request, limit),
            ).fetchall()
        try:
            values = tuple(ResearchRawManifest.model_validate_json(json.dumps(r[0])) for r in rows)
        except ValueError:
            raise DataFailure("unverified") from None
        if any((v.scope_id, v.request_id) != (scope, request) for v in values):
            raise DataFailure("unverified")
        return values

    @staticmethod
    def _replay(conn: psycopg.Connection[Any], scope: UUID, request: UUID) -> ResearchReplay | None:
        row = conn.execute(
            "SELECT payload,control_sha256 FROM qtrade_data.research_replay "
            "WHERE scope_id=%s AND request_id=%s",
            (scope, request),
        ).fetchone()
        if row is None:
            return None
        try:
            value = ResearchReplay.model_validate_json(json.dumps(row[0]))
        except ValueError:
            raise DataFailure("unverified") from None
        if (value.command.scope_id, value.command.request_id, value.control_sha256) != (
            scope,
            request,
            row[1],
        ):
            raise DataFailure("unverified")
        inputs = conn.execute(
            "SELECT batch_id FROM qtrade_data.research_replay_input "
            "WHERE scope_id=%s AND request_id=%s ORDER BY ordinal",
            (scope, request),
        ).fetchall()
        if tuple(r[0] for r in inputs) != tuple(b.batch_id for b in value.source_batches):
            raise DataFailure("unverified")
        return value

    def replay(self, scope: UUID, request: UUID) -> ResearchReplay | None:
        with self._connection() as conn:
            return self._replay(conn, scope, request)

    def enqueue_replay(self, value: ResearchReplay) -> None:
        value = ResearchReplay.model_validate_json(value.model_dump_json())
        scope, request = value.command.scope_id, value.command.request_id
        with self._connection() as conn:
            # Freeze the new run and intent before it becomes claimable. A crash
            # or metadata failure rolls the entire operation back, including job.
            self._register(conn, value.target_plan)
            if self._plan(conn, scope, value.command.parent_request_id) != value.parent_plan:
                raise DataFailure("conflict")
            existing = self._replay(conn, scope, request)
            if existing is not None:
                if existing != value:
                    raise DataFailure("conflict")
                return
            for batch in value.source_batches:
                row = conn.execute(
                    "SELECT manifest FROM qtrade_data.research_raw_batch "
                    "WHERE scope_id=%s AND batch_id=%s",
                    (scope, batch.batch_id),
                ).fetchone()
                if (
                    row is None
                    or ResearchRawManifest.model_validate_json(json.dumps(row[0])) != batch
                ):
                    raise DataFailure("conflict")
            conn.execute(
                "INSERT INTO qtrade_data.research_replay "
                "(scope_id,request_id,parent_request_id,control_sha256,payload) "
                "VALUES(%s,%s,%s,%s,%s::jsonb)",
                (
                    scope,
                    request,
                    value.command.parent_request_id,
                    value.control_sha256,
                    value.model_dump_json(),
                ),
            )
            for ordinal, batch in enumerate(value.source_batches):
                conn.execute(
                    "INSERT INTO qtrade_data.research_replay_input "
                    "(scope_id,request_id,batch_id,ordinal) VALUES(%s,%s,%s,%s)",
                    (scope, request, batch.batch_id, ordinal),
                )
            conn.execute(
                "INSERT INTO qtrade_data.research_job(scope_id,request_id) VALUES(%s,%s)",
                (scope, request),
            )
