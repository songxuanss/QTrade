"""Fail-closed live runtime launch gate."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.strategies import StrategyVersionApproval


@dataclass(frozen=True)
class LiveLaunchRequest:
    workspace_id: UUID
    account_id: UUID
    strategy_id: UUID
    strategy_version: int
    strategy_version_id: UUID
    strategy_author_id: UUID
    confirmation_text: str


class LiveRuntimePort(Protocol):
    def start(
        self,
        workspace_id: UUID,
        account_id: UUID,
        strategy_id: UUID,
        strategy_version: int,
        strategy_version_id: UUID,
        started_by: UUID,
        started_at: datetime,
    ) -> UUID: ...


class LiveLaunchEvidencePort(Protocol):
    def account_authorized(self, workspace_id: UUID, account_id: UUID) -> bool: ...

    def account_clear_for_live(self, workspace_id: UUID, account_id: UUID) -> bool: ...

    def approval(
        self, workspace_id: UUID, strategy_id: UUID, strategy_version: int
    ) -> StrategyVersionApproval | None: ...

    def rollback_plan_verified(
        self, workspace_id: UUID, account_id: UUID, strategy_version_id: UUID
    ) -> bool: ...


class LiveLaunchService:
    def __init__(
        self,
        runtime: LiveRuntimePort,
        evidence: LiveLaunchEvidencePort,
        *,
        feature_enabled: bool,
    ):
        self._runtime, self._evidence = runtime, evidence
        self._feature_enabled = feature_enabled

    def launch(
        self,
        access: WorkspaceAccess,
        request: LiveLaunchRequest,
        *,
        now: datetime,
    ) -> UUID:
        access.require(Permission.LIVE_START, object_workspace_id=request.workspace_id)
        if not self._feature_enabled:
            raise PortError(ErrorCode.REJECTED, "Live Runtime feature flag is disabled")
        if not self._evidence.account_authorized(request.workspace_id, request.account_id):
            raise PortError(ErrorCode.REJECTED, "live account is not authorized")
        if not self._evidence.account_clear_for_live(request.workspace_id, request.account_id):
            raise PortError(ErrorCode.REJECTED, "live account is blocked by a safety gate")
        approval = self._evidence.approval(
            request.workspace_id, request.strategy_id, request.strategy_version
        )
        if (
            approval is None
            or approval.workspace_id != request.workspace_id
            or approval.strategy_id != request.strategy_id
            or approval.version != request.strategy_version
            or approval.strategy_version_id != request.strategy_version_id
            or approval.approver_id in {access.user_id, request.strategy_author_id}
        ):
            raise PortError(ErrorCode.REJECTED, "exact Strategy Version lacks independent approval")
        if request.confirmation_text != "START LIVE":
            raise PortError(ErrorCode.REJECTED, "explicit live launch confirmation is required")
        if not self._evidence.rollback_plan_verified(
            request.workspace_id, request.account_id, request.strategy_version_id
        ):
            raise PortError(ErrorCode.REJECTED, "verified rollback plan is required")
        if now.tzinfo is None or now.utcoffset() is None or now < approval.approved_at:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid live launch time")
        return self._runtime.start(
            request.workspace_id,
            request.account_id,
            request.strategy_id,
            request.strategy_version,
            request.strategy_version_id,
            access.user_id,
            now,
        )
