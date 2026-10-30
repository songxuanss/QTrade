"""Transactional source/scope/run rotation shared by the two durable queues."""

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg import sql

from qtrade_data_core.ports import DataFailure


@dataclass(frozen=True)
class DispatchChoice:
    scope_id: UUID
    work_id: UUID
    run_id: UUID
    source: str


class FairDispatch:
    """Caller owns the transaction and must advance only after claiming the row.

    The short queue lock covers cursor registration, selection and lease update,
    never provider I/O. Work-row locks still use SKIP LOCKED. Queue-local turns
    certify claim opportunities, not equal wall time or cross-queue execution.
    """

    def __init__(self, conn: psycopg.Connection[Any], queue: Literal["collection", "research"]):
        self.conn, self.queue = conn, queue

    def choose(self, ready: sql.Composable, params: tuple[Any, ...]) -> DispatchChoice | None:
        self.conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("dispatch/" + self.queue,)
        )
        # Register once at the tail. Treating every unseen group as zero would
        # let an endless stream of new groups starve an existing active run.
        self.conn.execute(
            sql.SQL("""
            WITH ready AS MATERIALIZED ({}), keys AS (
                SELECT 'source' AS kind,source,'' AS scope_key,'' AS run_key FROM ready
                UNION SELECT 'scope',source,scope_id::text,'' FROM ready
                UNION SELECT 'run',source,scope_id::text,run_id::text FROM ready
            ) INSERT INTO qtrade_data.dispatch_cursor(queue,kind,source,scope_key,run_key)
            SELECT %s,k.kind,k.source,k.scope_key,k.run_key FROM keys k
            WHERE NOT EXISTS (SELECT 1 FROM qtrade_data.dispatch_cursor c
                WHERE c.queue=%s AND c.kind=k.kind AND c.source=k.source
                AND c.scope_key=k.scope_key AND c.run_key=k.run_key)
            ORDER BY k.kind,k.source,k.scope_key,k.run_key
            ON CONFLICT DO NOTHING
        """).format(ready),
            (*params, self.queue, self.queue),
        )
        table, identity = (
            ("collection_part", "part_id")
            if self.queue == "collection"
            else ("research_job", "request_id")
        )
        row = self.conn.execute(
            sql.SQL("""
            WITH ready AS MATERIALIZED ({})
            SELECT r.scope_id,r.work_id,r.run_id,r.source FROM ready r
            JOIN qtrade_data.{} p ON p.scope_id=r.scope_id AND p.{}=r.work_id
            JOIN qtrade_data.dispatch_cursor a ON a.queue=%s AND a.kind='source'
                AND a.source=r.source
            JOIN qtrade_data.dispatch_cursor b ON b.queue=a.queue AND b.kind='scope'
                AND b.source=r.source AND b.scope_key=r.scope_id::text
            JOIN qtrade_data.dispatch_cursor c ON c.queue=a.queue AND c.kind='run'
                AND c.source=r.source AND c.scope_key=r.scope_id::text
                AND c.run_key=r.run_id::text
            ORDER BY a.last_turn,b.last_turn,c.last_turn,r.due_at,r.work_id
            LIMIT 1 FOR UPDATE OF p SKIP LOCKED
        """).format(ready, sql.Identifier(table), sql.Identifier(identity)),
            (*params, self.queue),
        ).fetchone()
        return DispatchChoice(*row) if row else None

    def advance(self, chosen: DispatchChoice) -> None:
        updated = self.conn.execute(
            """
            WITH turn AS (SELECT nextval('qtrade_data.dispatch_turn') AS value)
            UPDATE qtrade_data.dispatch_cursor SET last_turn=turn.value FROM turn
            WHERE queue=%s AND source=%s AND (
                kind='source' OR (kind='scope' AND scope_key=%s)
                OR (kind='run' AND scope_key=%s AND run_key=%s))
        """,
            (
                self.queue,
                chosen.source,
                str(chosen.scope_id),
                str(chosen.scope_id),
                str(chosen.run_id),
            ),
        )
        if updated.rowcount != 3:
            raise DataFailure("conflict")
