from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade_api.risk import risk_router
from qtrade_application.risk import RiskService
from qtrade_contracts import RiskDecision, RiskReason
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.risk_limits import RiskLimit
from qtrade_domain.risk_records import RiskDecisionRecord


class _Store:
    def __init__(self, records):
        self.records = records

    def record(self, record):
        self.records.append(record)
        return record

    def get_for_intent(self, workspace_id, intent_id):
        return next(
            (
                row
                for row in self.records
                if row.decision.workspace_id == workspace_id and row.decision.intent_id == intent_id
            ),
            None,
        )

    def list(self, workspace_id, *, outcome, since, limit):
        return tuple(
            row
            for row in self.records
            if row.decision.workspace_id == workspace_id
            and (outcome is None or row.decision.outcome == outcome)
            and (since is None or row.decision.decided_at >= since)
        )[:limit]


class _Security:
    def __init__(self, access):
        self.access = access

    def workspace(self, _permission):
        return lambda: self.access


class _Limits:
    def __init__(self, rows):
        self.rows = rows

    def list(self, workspace_id):
        return tuple(self.rows)


def test_risk_decision_query_is_workspace_scoped_and_explains_denial():
    workspace_id = uuid4()
    access = WorkspaceAccess(
        Workspace(workspace_id, "Personal", "personal", None, False, False, datetime.now(UTC)),
        uuid4(),
        Role.VIEWER,
    )
    decision = RiskDecision(
        schema_version=1,
        workspace_id=workspace_id,
        decision_id=uuid4(),
        intent_id=uuid4(),
        decided_at=datetime.now(UTC),
        outcome="deny",
        rule_set_version="risk.v2",
        input_sha256="a" * 64,
        reasons=(
            RiskReason(
                rule_id="account.exposure",
                rule_version="limit.v3",
                code="limit_exceeded.account.notional_exposure",
                summary="Account exposure limit exceeded.",
                current_value="90",
                limit_value="100",
            ),
        ),
        trace_id=uuid4(),
    )
    store = _Store([RiskDecisionRecord(decision, '{"exposure":"110"}')])
    app = FastAPI()
    limit = RiskLimit(
        "account.exposure",
        "account",
        "main",
        "notional_exposure",
        Decimal("100"),
        "limits.v2",
    )
    app.include_router(risk_router(RiskService(store, _Limits([limit])), _Security(access)))

    response = TestClient(app).get(f"/api/v1/workspaces/{workspace_id}/risk/decisions?outcome=deny")

    assert response.status_code == 200
    body = response.json()[0]
    assert body["decision"]["reasons"][0]["rule_version"] == "limit.v3"
    assert datetime.fromisoformat(body["decision"]["decided_at"].replace("Z", "+00:00")) == (
        decision.decided_at
    )
    assert body["decision"]["input_sha256"] == "a" * 64
    assert body["input_summary"] == {"exposure": "110"}

    limits = TestClient(app).get(f"/api/v1/workspaces/{workspace_id}/risk/limits")
    assert limits.status_code == 200
    assert limits.json()[0]["version"] == "limits.v2"

    events = TestClient(app).get(f"/api/v1/workspaces/{workspace_id}/risk/events")
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "risk.deny"
