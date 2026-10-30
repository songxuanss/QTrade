"""Append-only PostgreSQL risk decision history."""

import json
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from qtrade_contracts import RiskDecision
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.risk_limits import LimitDimension, LimitMetric, RiskLimit
from qtrade_domain.risk_records import RiskDecisionRecord
from qtrade_postgres.connection import PostgresStore


class PostgresRiskDecisionStore(PostgresStore):
    def record(self, record: RiskDecisionRecord) -> RiskDecisionRecord:
        decision = record.decision
        payload = decision.model_dump_json()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO risk_decision (workspace_id,decision_id,intent_id,decided_at,"
                "outcome,input_sha256,rule_set_version,payload,input_summary) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb) "
                "ON CONFLICT DO NOTHING",
                (
                    decision.workspace_id,
                    decision.decision_id,
                    decision.intent_id,
                    decision.decided_at,
                    decision.outcome,
                    decision.input_sha256,
                    decision.rule_set_version,
                    payload,
                    record.input_summary_json,
                ),
            )
            row = conn.execute(
                "SELECT payload,input_summary FROM risk_decision "
                "WHERE workspace_id=%s AND intent_id=%s",
                (decision.workspace_id, decision.intent_id),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.UNAVAILABLE, "risk decision persistence unavailable")
        stored = _decode(row)
        if (
            stored.decision.outcome,
            stored.decision.rule_set_version,
            stored.decision.input_sha256,
            stored.decision.reasons,
            stored.input_summary_json,
        ) != (
            decision.outcome,
            decision.rule_set_version,
            decision.input_sha256,
            decision.reasons,
            record.input_summary_json,
        ):
            raise PortError(ErrorCode.CONFLICT, "risk intent evaluated with different inputs")
        return stored

    def get_for_intent(self, workspace_id: UUID, intent_id: UUID) -> RiskDecisionRecord | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload,input_summary FROM risk_decision "
                "WHERE workspace_id=%s AND intent_id=%s",
                (workspace_id, intent_id),
            ).fetchone()
        return _decode(row) if row else None

    def list(
        self,
        workspace_id: UUID,
        *,
        outcome: str | None,
        since: datetime | None,
        limit: int,
    ) -> tuple[RiskDecisionRecord, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid risk decision limit")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT payload,input_summary FROM risk_decision WHERE workspace_id=%s "
                "AND (%s::text IS NULL OR outcome=%s::text) "
                "AND (%s::timestamptz IS NULL OR decided_at >= %s::timestamptz) "
                "ORDER BY decided_at DESC,decision_id LIMIT %s",
                (workspace_id, outcome, outcome, since, since, limit),
            ).fetchall()
        return tuple(_decode(row) for row in rows)

    def list_limits(self, workspace_id: UUID) -> tuple[RiskLimit, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT limit_id,dimension,scope_id,metric,maximum,version FROM risk_limit "
                "WHERE workspace_id=%s AND active ORDER BY dimension,scope_id,metric,limit_id",
                (workspace_id,),
            ).fetchall()
        try:
            return tuple(
                RiskLimit(
                    str(row[0]),
                    cast(LimitDimension, str(row[1])),
                    str(row[2]),
                    cast(LimitMetric, str(row[3])),
                    Decimal(str(row[4])),
                    str(row[5]),
                )
                for row in rows
            )
        except (TypeError, ValueError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored risk limit") from None


class PostgresRiskLimitStore(PostgresStore):
    def list(self, workspace_id: UUID) -> tuple[RiskLimit, ...]:
        return PostgresRiskDecisionStore(self._url, schema=self._schema).list_limits(workspace_id)


def _decode(row: tuple[object, ...]) -> RiskDecisionRecord:
    try:
        decision = RiskDecision.model_validate_json(json.dumps(row[0]))
        summary = json.dumps(row[1], separators=(",", ":"), sort_keys=True)
        return RiskDecisionRecord(decision, summary)
    except (TypeError, ValueError, PortError):
        raise PortError(ErrorCode.UNAVAILABLE, "invalid stored risk decision") from None
