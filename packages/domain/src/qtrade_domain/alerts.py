"""Workspace-owned alert rule definitions and immutable revisions."""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from qtrade_domain.alert_condition import validate_condition
from qtrade_domain.ports import ErrorCode, NotificationChannel, PortError


@dataclass(frozen=True, slots=True)
class AlertRuleVersion:
    rule_id: UUID
    workspace_id: UUID
    version: int
    name: str
    source: str
    condition: str
    severity: str
    cooldown_seconds: int
    enabled: bool
    created_at: datetime
    notification_targets: tuple[tuple[NotificationChannel, str], ...] = (("in_app", "workspace"),)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.rule_id, UUID)
            or not isinstance(self.workspace_id, UUID)
            or type(self.version) is not int
            or self.version < 1
            or not isinstance(self.name, str)
            or not 1 <= len(self.name.strip()) <= 120
            or self.source not in {"market", "risk", "data", "system"}
            or not isinstance(self.condition, str)
            or not 1 <= len(self.condition.strip()) <= 512
            or self.severity not in {"info", "warning", "critical"}
            or type(self.cooldown_seconds) is not int
            or not 0 <= self.cooldown_seconds <= 86400
            or type(self.enabled) is not bool
            or not isinstance(self.created_at, datetime)
            or self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
            or not isinstance(self.notification_targets, tuple)
            or not 1 <= len(self.notification_targets) <= 8
            or any(
                not isinstance(target, tuple)
                or len(target) != 2
                or target[0] not in {"in_app", "email", "webhook"}
                or not isinstance(target[1], str)
                or not 1 <= len(target[1]) <= 320
                for target in self.notification_targets
            )
            or len(set(self.notification_targets)) != len(self.notification_targets)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert rule")
        validate_condition(self.condition)


class AlertSource(StrEnum):
    MARKET = "market"
    RISK = "risk"
    DATA = "data"
    SYSTEM = "system"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"


AlertAction = Literal["claim", "acknowledge", "silence", "close", "note"]


@dataclass(frozen=True, slots=True)
class AlertEvent:
    event_id: UUID
    workspace_id: UUID
    source: AlertSource
    source_event_id: str
    event_type: str
    severity: AlertSeverity
    status: AlertStatus
    title: str
    message: str
    resource_id: str | None
    occurred_at: datetime
    updated_at: datetime
    context_snapshot_json: str = "{}"
    assignee_id: UUID | None = None
    silenced_until: datetime | None = None

    def __post_init__(self) -> None:
        canonical_context = _canonical_json_object(self.context_snapshot_json)
        if (
            not isinstance(self.event_id, UUID)
            or not isinstance(self.workspace_id, UUID)
            or not isinstance(self.source, AlertSource)
            or not isinstance(self.source_event_id, str)
            or not 1 <= len(self.source_event_id) <= 256
            or not isinstance(self.event_type, str)
            or not 1 <= len(self.event_type) <= 128
            or not isinstance(self.severity, AlertSeverity)
            or not isinstance(self.status, AlertStatus)
            or not isinstance(self.title, str)
            or not 1 <= len(self.title.strip()) <= 160
            or not isinstance(self.message, str)
            or not 1 <= len(self.message.strip()) <= 4096
            or (
                self.resource_id is not None
                and (not isinstance(self.resource_id, str) or len(self.resource_id) > 256)
            )
            or not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
            or not isinstance(self.updated_at, datetime)
            or self.updated_at.tzinfo is None
            or self.updated_at.utcoffset() is None
            or self.updated_at < self.occurred_at
            or not isinstance(self.context_snapshot_json, str)
            or len(self.context_snapshot_json) > 65536
            or canonical_context is None
            or (self.assignee_id is not None and not isinstance(self.assignee_id, UUID))
            or (
                self.silenced_until is not None
                and (
                    not isinstance(self.silenced_until, datetime)
                    or self.silenced_until.tzinfo is None
                    or self.silenced_until.utcoffset() is None
                )
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert event")
        object.__setattr__(self, "context_snapshot_json", canonical_context)


@dataclass(frozen=True, slots=True)
class AlertDisposition:
    event_id: UUID
    workspace_id: UUID
    actor_id: UUID
    action: AlertAction
    note: str | None
    silenced_until: datetime | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.event_id, UUID)
            or not isinstance(self.workspace_id, UUID)
            or not isinstance(self.actor_id, UUID)
            or self.action not in {"claim", "acknowledge", "silence", "close", "note"}
            or (self.note is not None and (not isinstance(self.note, str) or len(self.note) > 2048))
            or (
                self.silenced_until is not None
                and (
                    not isinstance(self.silenced_until, datetime)
                    or self.silenced_until.tzinfo is None
                    or self.silenced_until.utcoffset() is None
                )
            )
            or not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
            or (self.action == "silence") != (self.silenced_until is not None)
            or (self.action == "note" and (self.note is None or not self.note.strip()))
            or (self.note is not None and not self.note.strip())
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert disposition")


def _canonical_json_object(value: str) -> str | None:
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            return None
        return json.dumps(parsed, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        return None


class AlertRulePort(Protocol):
    """Persistence boundary for append-only rule revisions."""

    def create(self, rule: AlertRuleVersion) -> AlertRuleVersion: ...

    def current(self, workspace_id: UUID, rule_id: UUID) -> AlertRuleVersion | None: ...

    def append(self, rule: AlertRuleVersion) -> AlertRuleVersion: ...

    def list(self, workspace_id: UUID) -> tuple[AlertRuleVersion, ...]: ...

    def versions(self, workspace_id: UUID, rule_id: UUID) -> tuple[AlertRuleVersion, ...]: ...


class AlertEventPort(Protocol):
    def record(self, event: AlertEvent) -> AlertEvent: ...

    def list(self, workspace_id: UUID, limit: int) -> tuple[AlertEvent, ...]: ...

    def get(self, workspace_id: UUID, event_id: UUID) -> AlertEvent | None: ...

    def handle(
        self,
        actor_id: UUID,
        workspace_id: UUID,
        event_id: UUID,
        action: AlertAction,
        occurred_at: datetime,
        note: str | None = None,
        silenced_until: datetime | None = None,
    ) -> AlertEvent: ...

    def history(self, workspace_id: UUID, event_id: UUID) -> tuple[AlertDisposition, ...]: ...


class AlertEvaluationPort(Protocol):
    def decision(
        self,
        workspace_id: UUID,
        rule_id: UUID,
        triggered: bool,
        now: datetime,
        cooldown_seconds: int,
        severity: AlertSeverity,
        escalation_after_seconds: int,
        silenced: bool,
    ) -> AlertSeverity | None: ...
