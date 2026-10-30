"""Leased delivery records and retry scheduling for notification channels."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from qtrade_domain.ports import ErrorCode, Notification, NotificationChannel, PortError

NotificationAttemptStatus = Literal["delivered", "retrying", "failed"]


@dataclass(frozen=True, slots=True)
class NotificationDeliveryLease:
    notification: Notification
    token: UUID
    attempt: int


@dataclass(frozen=True, slots=True)
class NotificationAttempt:
    workspace_id: UUID
    notification_id: UUID
    channel: NotificationChannel
    target: str
    status: NotificationAttemptStatus
    response_summary: str
    attempted_at: datetime
    attempt: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.notification_id, UUID)
            or self.channel not in {"in_app", "email", "webhook"}
            or not isinstance(self.target, str)
            or not 1 <= len(self.target) <= 320
            or self.status not in {"delivered", "retrying", "failed"}
            or not isinstance(self.response_summary, str)
            or len(self.response_summary) > 512
            or not isinstance(self.attempted_at, datetime)
            or self.attempted_at.tzinfo is None
            or self.attempted_at.utcoffset() is None
            or type(self.attempt) is not int
            or self.attempt < 1
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid notification attempt")


class NotificationDeliveryQueuePort(Protocol):
    def enqueue(self, notification: Notification, now: datetime) -> None: ...

    def ready(
        self, now: datetime, limit: int, lease_seconds: int
    ) -> tuple[NotificationDeliveryLease, ...]: ...

    def finish(
        self,
        lease: NotificationDeliveryLease,
        attempt: NotificationAttempt,
        *,
        retry_after_seconds: int | None,
        max_attempts: int = 8,
    ) -> bool: ...

    def attempts(
        self, workspace_id: UUID, notification_id: UUID
    ) -> tuple[NotificationAttempt, ...]: ...
