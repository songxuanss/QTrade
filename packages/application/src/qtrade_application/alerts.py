"""Use cases for versioned, workspace-scoped alert rules."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from qtrade_domain.alerts import (
    AlertAction,
    AlertDisposition,
    AlertEvent,
    AlertEventPort,
    AlertRulePort,
    AlertRuleVersion,
    AlertSeverity,
    AlertSource,
    AlertStatus,
)
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, NotificationChannel, PortError


class AlertRuleService:
    def __init__(self, rules: AlertRulePort):
        self._rules = rules

    def list(self, access: WorkspaceAccess) -> tuple[AlertRuleVersion, ...]:
        access.require(Permission.READ)
        return self._rules.list(access.workspace.workspace_id)

    def rules_for_evaluation(self, workspace_id: UUID) -> tuple[AlertRuleVersion, ...]:
        """Read the scoped rules for the trusted alert evaluator process."""
        return self._rules.list(workspace_id)

    def versions(self, access: WorkspaceAccess, rule_id: UUID) -> tuple[AlertRuleVersion, ...]:
        access.require(Permission.READ)
        rows = self._rules.versions(access.workspace.workspace_id, rule_id)
        if not rows:
            raise PortError(ErrorCode.NOT_FOUND, "alert rule unavailable")
        return rows

    def create(
        self,
        access: WorkspaceAccess,
        *,
        name: str,
        source: str,
        condition: str,
        severity: str,
        cooldown_seconds: int,
        enabled: bool = True,
        notification_targets: tuple[tuple[NotificationChannel, str], ...] = (
            ("in_app", "workspace"),
        ),
    ) -> AlertRuleVersion:
        access.require(Permission.ALERT_RULE_WRITE)
        rule = AlertRuleVersion(
            uuid4(),
            access.workspace.workspace_id,
            1,
            name,
            source,
            condition,
            severity,
            cooldown_seconds,
            enabled,
            datetime.now(UTC),
            notification_targets,
        )
        return self._rules.create(rule)

    def set_enabled(
        self, access: WorkspaceAccess, rule_id: UUID, enabled: bool
    ) -> AlertRuleVersion:
        access.require(Permission.ALERT_RULE_WRITE)
        if type(enabled) is not bool:
            raise PortError(ErrorCode.INVALID_INPUT, "enabled must be boolean")
        workspace_id = access.workspace.workspace_id
        current = self._rules.current(workspace_id, rule_id)
        if current is None:
            raise PortError(ErrorCode.NOT_FOUND, "alert rule unavailable")
        if current.enabled == enabled:
            return current
        return self._rules.append(
            replace(
                current,
                version=current.version + 1,
                enabled=enabled,
                created_at=datetime.now(UTC),
            )
        )


@dataclass(frozen=True, slots=True)
class AlertEventFacts:
    workspace_id: UUID
    source_event_id: str
    event_type: str
    severity: AlertSeverity
    title: str
    message: str
    occurred_at: datetime
    resource_id: str | None = None
    context_snapshot_json: str = "{}"


class AlertEventService:
    """Normalize source events into one workspace-scoped alert stream."""

    def __init__(self, events: AlertEventPort):
        self._events = events

    def market(self, facts: AlertEventFacts) -> AlertEvent:
        return self._record(AlertSource.MARKET, facts)

    def risk(self, facts: AlertEventFacts) -> AlertEvent:
        return self._record(AlertSource.RISK, facts)

    def data(self, facts: AlertEventFacts) -> AlertEvent:
        return self._record(AlertSource.DATA, facts)

    def system(self, facts: AlertEventFacts) -> AlertEvent:
        return self._record(AlertSource.SYSTEM, facts)

    def list(self, access: WorkspaceAccess, limit: int = 100) -> tuple[AlertEvent, ...]:
        access.require(Permission.READ)
        return self._events.list(access.workspace.workspace_id, limit)

    def detail(self, access: WorkspaceAccess, event_id: UUID) -> AlertEvent:
        access.require(Permission.READ)
        event = self._events.get(access.workspace.workspace_id, event_id)
        if event is None:
            raise PortError(ErrorCode.NOT_FOUND, "alert event unavailable")
        return event

    def history(self, access: WorkspaceAccess, event_id: UUID) -> tuple[AlertDisposition, ...]:
        self.detail(access, event_id)
        return self._events.history(access.workspace.workspace_id, event_id)

    def handle(
        self,
        access: WorkspaceAccess,
        event_id: UUID,
        action: AlertAction,
        *,
        now: datetime,
        note: str | None = None,
        silenced_until: datetime | None = None,
    ) -> AlertEvent:
        access.require(Permission.ALERT_HANDLE)
        if now.tzinfo is None or now.utcoffset() is None:
            raise PortError(ErrorCode.INVALID_INPUT, "timezone-aware action time required")
        if action == "silence" and (
            silenced_until is None
            or silenced_until <= now
            or (silenced_until - now).total_seconds() > 30 * 86400
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "silence must end within 30 days")
        if action != "silence" and silenced_until is not None:
            raise PortError(ErrorCode.INVALID_INPUT, "silence expiry only applies to silence")
        return self._events.handle(
            access.user_id,
            access.workspace.workspace_id,
            event_id,
            action,
            now,
            note,
            silenced_until,
        )

    def _record(
        self,
        source: AlertSource,
        facts: AlertEventFacts,
    ) -> AlertEvent:
        event = AlertEvent(
            uuid4(),
            facts.workspace_id,
            source,
            facts.source_event_id,
            facts.event_type,
            facts.severity,
            AlertStatus.OPEN,
            facts.title,
            facts.message,
            facts.resource_id,
            facts.occurred_at,
            facts.occurred_at,
            facts.context_snapshot_json,
        )
        return self._events.record(event)
