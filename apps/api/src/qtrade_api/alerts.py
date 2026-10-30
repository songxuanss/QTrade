"""Workspace-scoped alert rule lifecycle API."""

import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from qtrade_api.audit_context import request_context
from qtrade_api.security import ApiSecurity, public_error
from qtrade_application.alert_jobs import AlertEvaluationSubmissionService
from qtrade_application.alerts import AlertEventService, AlertRuleService
from qtrade_domain.alerts import AlertDisposition, AlertEvent, AlertRuleVersion
from qtrade_domain.audit_port import AuditDetails, AuditRecord, AuditStorePort, contextualize
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import PortError


class NotificationTargetInput(BaseModel):
    channel: Literal["in_app", "email", "webhook"]
    destination_id: str = Field(min_length=1, max_length=320)


class CreateRule(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(pattern="^(market|risk|data|system)$")
    condition: str = Field(min_length=1, max_length=512)
    severity: str = Field(pattern="^(info|warning|critical)$")
    cooldown_seconds: int = Field(ge=0, le=86400)
    enabled: bool = True
    notification_targets: list[NotificationTargetInput] = Field(
        default_factory=lambda: [
            NotificationTargetInput(channel="in_app", destination_id="workspace")
        ],
        min_length=1,
        max_length=8,
    )


class EnableRule(BaseModel):
    enabled: bool


class SubmitAlertEvaluation(BaseModel):
    source: str = Field(pattern="^(market|risk|data|system)$")
    facts: dict[str, str | int | float | bool] = Field(min_length=1, max_length=64)
    occurred_at: datetime | None = None
    resource_id: str | None = Field(default=None, max_length=256)
    context_snapshot: dict[str, object] = Field(default_factory=dict)


class AlertActionInput(BaseModel):
    note: str | None = Field(default=None, max_length=2048)
    silenced_until: datetime | None = None


def alert_rule_router(
    service: AlertRuleService | None,
    security: ApiSecurity,
    audit: AuditStorePort | None = None,
    events: AlertEventService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/alert-rules", tags=["alerting"])

    def configured() -> AlertRuleService:
        if service is None:
            raise HTTPException(
                503, "alert rules unavailable", headers={"Cache-Control": "no-store"}
            )
        return service

    @router.get("/events")
    def list_events(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[dict[str, object]]:
        if events is None:
            raise HTTPException(
                503, "alert events unavailable", headers={"Cache-Control": "no-store"}
            )
        try:
            return [_event_payload(item) for item in events.list(access, limit)]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/events/{event_id}")
    def event_detail(
        event_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> dict[str, object]:
        if events is None:
            raise HTTPException(503, "alert events unavailable")
        try:
            return _event_payload(events.detail(access, event_id))
        except PortError as error:
            raise public_error(error) from None

    @router.get("/events/{event_id}/history")
    def event_history(
        event_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        if events is None:
            raise HTTPException(503, "alert events unavailable")
        try:
            return [_disposition_payload(item) for item in events.history(access, event_id)]
        except PortError as error:
            raise public_error(error) from None

    @router.post("/events/{event_id}/actions/{action}")
    def handle_event(
        event_id: UUID,
        action: Literal["claim", "acknowledge", "silence", "close", "note"],
        body: AlertActionInput,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.ALERT_HANDLE, mutate=True))
        ],
    ) -> dict[str, object]:
        if events is None:
            raise HTTPException(503, "alert events unavailable")
        try:
            event = events.handle(
                access,
                event_id,
                action,
                now=datetime.now(UTC),
                note=body.note,
                silenced_until=body.silenced_until,
            )
            _audit(audit, access, request, f"alert.event.{action}", event_id)
            return _event_payload(event)
        except PortError as error:
            raise public_error(error) from None

    @router.get("")
    def list_rules(
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        try:
            return [_payload(rule) for rule in configured().list(access)]
        except PortError as error:
            raise public_error(error) from None

    @router.get("/{rule_id}/versions")
    def versions(
        rule_id: UUID,
        access: Annotated[WorkspaceAccess, Depends(security.workspace(Permission.READ))],
    ) -> list[dict[str, object]]:
        try:
            return [_payload(rule) for rule in configured().versions(access, rule_id)]
        except PortError as error:
            raise public_error(error) from None

    @router.post("", status_code=201)
    def create(
        body: CreateRule,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.ALERT_RULE_WRITE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            values = body.model_dump(exclude={"notification_targets"})
            rule = configured().create(
                access,
                **values,
                notification_targets=tuple(
                    (target.channel, target.destination_id) for target in body.notification_targets
                ),
            )
            _audit(audit, access, request, "alert.rule.create", rule.rule_id)
            return _payload(rule)
        except PortError as error:
            raise public_error(error) from None

    @router.patch("/{rule_id}/enabled")
    def set_enabled(
        rule_id: UUID,
        body: EnableRule,
        request: Request,
        access: Annotated[
            WorkspaceAccess, Depends(security.workspace(Permission.ALERT_RULE_WRITE, mutate=True))
        ],
    ) -> dict[str, object]:
        try:
            rule = configured().set_enabled(access, rule_id, body.enabled)
            _audit(audit, access, request, "alert.rule.enabled.change", rule.rule_id)
            return _payload(rule)
        except PortError as error:
            raise public_error(error) from None

    return router


def alert_evaluation_router(
    service: AlertEvaluationSubmissionService | None,
    security: ApiSecurity,
    audit: AuditStorePort | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/workspaces/{workspace_id}/alert-evaluations", tags=["alerting"]
    )

    @router.post("", status_code=202)
    def submit(
        body: SubmitAlertEvaluation,
        request: Request,
        access: Annotated[
            WorkspaceAccess,
            Depends(security.workspace(Permission.ALERT_RULE_WRITE, mutate=True)),
        ],
    ) -> dict[str, object]:
        if service is None:
            raise HTTPException(
                503, "alert evaluation unavailable", headers={"Cache-Control": "no-store"}
            )
        try:
            from qtrade_domain.alerts import AlertSource

            job = service.submit(
                access,
                AlertSource(body.source),
                body.facts,
                occurred_at=body.occurred_at,
                resource_id=body.resource_id,
                context_snapshot=body.context_snapshot,
            )
            _audit(audit, access, request, "alert.evaluation.submit", job.job_id)
            return {
                "job_id": str(job.job_id),
                "status": "queued",
                "created_at": datetime.now(UTC).isoformat(),
            }
        except PortError as error:
            raise public_error(error) from None

    return router


def _payload(rule: AlertRuleVersion) -> dict[str, object]:
    return {
        "rule_id": str(rule.rule_id),
        "workspace_id": str(rule.workspace_id),
        "version": rule.version,
        "name": rule.name,
        "source": rule.source,
        "condition": rule.condition,
        "severity": rule.severity,
        "cooldown_seconds": rule.cooldown_seconds,
        "enabled": rule.enabled,
        "created_at": rule.created_at.isoformat(),
        "notification_targets": [
            {"channel": channel, "destination_id": target}
            for channel, target in rule.notification_targets
        ],
    }


def _event_payload(event: AlertEvent) -> dict[str, object]:
    return {
        "event_id": str(event.event_id),
        "workspace_id": str(event.workspace_id),
        "source": event.source.value,
        "source_event_id": event.source_event_id,
        "event_type": event.event_type,
        "severity": event.severity.value,
        "status": event.status.value,
        "title": event.title,
        "message": event.message,
        "resource_id": event.resource_id,
        "occurred_at": event.occurred_at.isoformat(),
        "updated_at": event.updated_at.isoformat(),
        "context_snapshot": json.loads(event.context_snapshot_json),
        "assignee_id": str(event.assignee_id) if event.assignee_id else None,
        "silenced_until": event.silenced_until.isoformat() if event.silenced_until else None,
    }


def _disposition_payload(item: AlertDisposition) -> dict[str, object]:
    return {
        "event_id": str(item.event_id),
        "workspace_id": str(item.workspace_id),
        "actor_id": str(item.actor_id),
        "action": item.action,
        "note": item.note,
        "silenced_until": item.silenced_until.isoformat() if item.silenced_until else None,
        "occurred_at": item.occurred_at.isoformat(),
    }


def _audit(
    store: AuditStorePort | None,
    access: WorkspaceAccess,
    request: Request,
    action: str,
    rule_id: UUID,
) -> None:
    if store is None:
        return
    store.append(
        contextualize(
            AuditRecord(
                access.workspace.workspace_id,
                "user",
                access.user_id,
                action,
                "success",
                datetime.now(UTC),
                "api",
                uuid4(),
                details=AuditDetails(target_id=rule_id),
            ),
            request_context(request, action),
        )
    )
