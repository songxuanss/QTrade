"""Append-only, plan-bound response receipts with bounded scoped keyset reads."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.research_acquisition import ResearchResponsePage, ResearchResponseReceipt
from qtrade_data_contracts.research_transport import ResearchPlan
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research_acquisition import matches_plan
from qtrade_data_postgres.research import PostgresResearchEvidence


class PostgresResearchResponses:
    def __init__(self, url: str):
        self.repository = PostgresResearchEvidence(url)

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        try:
            with psycopg.connect(self.repository.url, connect_timeout=5) as conn:
                conn.execute("SET LOCAL statement_timeout='10s'")
                conn.execute("SET LOCAL lock_timeout='5s'")
                yield conn
        except psycopg.Error:
            raise DataFailure("unavailable") from None

    @staticmethod
    def _read(row: tuple[str, Any], plan: ResearchPlan) -> ResearchResponseReceipt:
        try:
            receipt = ResearchResponseReceipt.model_validate_json(json.dumps(row[1]))
        except (ValueError, TypeError):
            raise DataFailure("unverified") from None
        if digest(receipt.model_dump(mode="json")) != row[0] or not matches_plan(receipt, plan):
            raise DataFailure("unverified")
        return receipt

    def append(self, plan: ResearchPlan, receipt: ResearchResponseReceipt) -> None:
        receipt = ResearchResponseReceipt.model_validate_json(receipt.model_dump_json())
        if not matches_plan(receipt, plan):
            raise DataFailure("conflict")
        with self._connection() as conn:
            if self.repository._plan(conn, receipt.scope_id, receipt.request_id) != plan:
                raise DataFailure("conflict")
            content = digest(receipt.model_dump(mode="json"))
            conn.execute(
                """INSERT INTO qtrade_data.research_response_receipt
                (scope_id,request_id,receipt_id,acquisition_id,response_index,captured_at,payload_sha256,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT DO NOTHING""",
                (
                    receipt.scope_id,
                    receipt.request_id,
                    receipt.receipt_id,
                    receipt.acquisition_id,
                    receipt.response_index,
                    receipt.captured_at,
                    content,
                    receipt.model_dump_json(),
                ),
            )
            row = conn.execute(
                """SELECT payload_sha256,payload FROM qtrade_data.research_response_receipt
                WHERE scope_id=%s AND receipt_id=%s""",
                (receipt.scope_id, receipt.receipt_id),
            ).fetchone()
            if row is None or self._read(row, plan) != receipt:
                raise DataFailure("conflict")

    def page(self, plan: ResearchPlan, limit: int, after: UUID | None) -> ResearchResponsePage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DataFailure("invalid_input")
        with self._connection() as conn:
            before = None
            if after is not None:
                cursor_row = conn.execute(
                    """SELECT captured_at FROM qtrade_data.research_response_receipt
                    WHERE scope_id=%s AND request_id=%s AND receipt_id=%s""",
                    (plan.request.scope_id, plan.request.request_id, after),
                ).fetchone()
                if cursor_row is None:
                    raise DataFailure("invalid_input")
                before = cursor_row[0]
            rows = conn.execute(
                """SELECT payload_sha256,payload FROM qtrade_data.research_response_receipt
                WHERE scope_id=%s AND request_id=%s
                AND (%s::timestamptz IS NULL OR (captured_at,receipt_id)<(%s,%s))
                ORDER BY captured_at DESC,receipt_id DESC LIMIT %s""",
                (plan.request.scope_id, plan.request.request_id, before, before, after, limit + 1),
            ).fetchall()
        receipts = tuple(self._read(row, plan) for row in rows[:limit])
        return ResearchResponsePage(
            scope_id=plan.request.scope_id,
            request_id=plan.request.request_id,
            receipts=receipts,
            next_id=receipts[-1].receipt_id if len(rows) > limit else None,
        )
