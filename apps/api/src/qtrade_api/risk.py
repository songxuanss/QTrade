"""Workspace-scoped, read-only risk decision history API."""

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.risk import RiskService
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import PortError


def risk_router(service: RiskService | None, security: ApiSecurity) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/risk", tags=["risk"])

    @router.get("/decisions")
    def decisions(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        outcome: Annotated[str | None, Query(pattern="^(allow|deny|require_approval)$")] = None,
        since: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, object]]:
        if service is None:
            raise HTTPException(503, "risk history unavailable")
        try:
            records = service.list_decisions(access, outcome=outcome, since=since, limit=limit)
            return [
                {
                    "decision": record.decision.model_dump(mode="json"),
                    "input_summary": json.loads(record.input_summary_json),
                }
                for record in records
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/limits")
    def limits(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        if service is None:
            raise HTTPException(503, "risk limits unavailable")
        try:
            return [
                {
                    "limit_id": item.limit_id,
                    "dimension": item.dimension,
                    "scope_id": item.scope_id,
                    "metric": item.metric,
                    "maximum": str(item.maximum),
                    "version": item.version,
                }
                for item in service.list_limits(access)
            ]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/events")
    def events(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, object]]:
        if service is None:
            raise HTTPException(503, "risk events unavailable")
        try:
            rows = service.list_decisions(access, limit=limit)
            return [
                {
                    "event_id": str(record.decision.decision_id),
                    "workspace_id": str(record.decision.workspace_id),
                    "event_type": f"risk.{record.decision.outcome}",
                    "occurred_at": record.decision.decided_at.isoformat(),
                    "resource_id": str(record.decision.intent_id),
                    "input_sha256": record.decision.input_sha256,
                    "rule_set_version": record.decision.rule_set_version,
                    "reasons": [
                        reason.model_dump(mode="json") for reason in record.decision.reasons
                    ],
                    "input_summary": json.loads(record.input_summary_json),
                }
                for record in rows
            ]
        except PortError as error:
            raise public_error(error) from None

    return router
