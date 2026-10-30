"""Atomic replay metadata + existing leased queue, with no separate job engine."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg

from qtrade_data_contracts.collection_replay import CollectionReplay
from qtrade_data_core.collection_replay import parent_reference, target_part
from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.jobs import PostgresJobs


class PostgresCollectionReplays(PostgresJobs):
    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            if self.conn is not None:
                yield self.conn
            else:
                with psycopg.connect(self.url, connect_timeout=5) as conn:
                    conn.execute("SET LOCAL statement_timeout='10s'")
                    conn.execute("SET LOCAL lock_timeout='5s'")
                    yield conn
        except psycopg.Error:
            raise DataFailure("unavailable") from None

    def replay(self, scope: UUID, request: UUID) -> CollectionReplay | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload,control_sha256,part_id,parent_job_id,parent_part_id,"
                "root_job_id,root_part_id,input_id FROM qtrade_data.collection_replay "
                "WHERE scope_id=%s AND request_id=%s",
                (scope, request),
            ).fetchone()
        if row is None:
            return None
        try:
            value = CollectionReplay.model_validate_json(json.dumps(row[0]))
        except ValueError:
            raise DataFailure("unverified") from None
        if (
            value.command.scope_id,
            value.command.request_id,
            value.control_sha256,
            value.target_part_id,
            value.command.parent_job_id,
            value.command.parent_part_id,
            value.input.owner_job_id,
            value.input.part_id,
            value.input.input_id,
        ) != (scope, request, *row[1:]):
            raise DataFailure("unverified")
        return value

    def enqueue_replay(self, value: CollectionReplay) -> dict[str, object]:
        value = CollectionReplay.model_validate_json(value.model_dump_json())
        cmd = value.command
        with self._connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(cmd.scope_id),)
            )
            store = PostgresCollectionReplays(self.url, conn)
            old = store.replay(cmd.scope_id, cmd.request_id)
            if old is not None:
                if old != value:
                    raise DataFailure("conflict")
                return store.job(cmd.scope_id, cmd.request_id)
            if store.find(cmd.scope_id, cmd.request_id) is not None:
                raise DataFailure("conflict")
            parent = store.part_for_job(cmd.scope_id, cmd.parent_job_id, cmd.parent_part_id)
            if parent_reference(parent) != value.parent:
                raise DataFailure("conflict")
            # File input integrity is checked by preview and again by execution.
            # This transaction freezes queue ownership and the selected lineage.
            definitions = (value.input.factor_definition,) if value.input.factor_definition else ()
            result = store.submit(
                cmd.request_id,
                value.target_request,
                (target_part(value),),
                definitions,
                {value.input.instrument_id: value.input.instrument_name},
            )
            conn.execute(
                "INSERT INTO qtrade_data.collection_replay "
                "(scope_id,request_id,part_id,parent_job_id,parent_part_id,root_job_id,root_part_id,"
                "input_id,control_sha256,payload) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (
                    cmd.scope_id,
                    cmd.request_id,
                    value.target_part_id,
                    cmd.parent_job_id,
                    cmd.parent_part_id,
                    value.input.owner_job_id,
                    value.input.part_id,
                    value.input.input_id,
                    value.control_sha256,
                    value.model_dump_json(),
                ),
            )
            return result
