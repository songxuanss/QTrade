"""Workspace and account trading safety switch boundaries."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class KillSwitch:
    workspace_id: UUID
    account_id: UUID | None
    active: bool
    reason: str
    changed_by: UUID
    changed_at: datetime
    revision: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or (self.account_id is not None and not isinstance(self.account_id, UUID))
            or type(self.active) is not bool
            or not isinstance(self.reason, str)
            or not 1 <= len(self.reason.strip()) <= 500
            or not isinstance(self.changed_by, UUID)
            or not isinstance(self.changed_at, datetime)
            or self.changed_at.utcoffset() is None
            or type(self.revision) is not int
            or self.revision < 1
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid kill switch state")


class KillSwitchStore(Protocol):
    def is_active(self, workspace_id: UUID, account_id: UUID | None = None) -> bool: ...

    def set_state(
        self,
        state: KillSwitch,
        *,
        expected_revision: int | None,
    ) -> KillSwitch: ...

    def get_state(
        self, workspace_id: UUID, account_id: UUID | None = None
    ) -> KillSwitch | None: ...

    def approve_recovery(
        self,
        request: "RecoveryRequest",
        *,
        approver_id: UUID,
        reason: str,
        now: datetime,
    ) -> KillSwitch: ...


@dataclass(frozen=True)
class RecoveryRequest:
    request_id: UUID
    workspace_id: UUID
    account_id: UUID | None
    requested_by: UUID
    requested_at: datetime
    switch_revision: int | None = None


class RecoveryRequestStore(Protocol):
    def create(self, request: RecoveryRequest) -> None: ...

    def get_request(self, request_id: UUID) -> RecoveryRequest | None: ...
    def list_pending(self, workspace_id: UUID) -> tuple[RecoveryRequest, ...]: ...

    def consume(self, request_id: UUID, *, approver_id: UUID, now: datetime) -> RecoveryRequest: ...
