"""Audited kill switch operation and independent recovery approval."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from qtrade_domain.audit_port import AuditDetails, AuditRecord, AuditStorePort
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.kill_switch import (
    KillSwitch,
    KillSwitchStore,
    RecoveryRequest,
    RecoveryRequestStore,
)
from qtrade_domain.ports import ErrorCode, PortError


class KillSwitchTrigger(StrEnum):
    MANUAL = "manual"
    DAILY_LOSS = "daily_loss"
    DATA_INTEGRITY = "data_integrity"
    CONSECUTIVE_REJECTIONS = "consecutive_rejections"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"


class KillSwitchService:
    def __init__(
        self,
        states: KillSwitchStore,
        recoveries: RecoveryRequestStore,
        audit: AuditStorePort,
    ):
        self._states, self._recoveries, self._audit = states, recoveries, audit

    def activate(
        self,
        access: WorkspaceAccess,
        *,
        account_id: UUID | None,
        reason: str,
        trigger: KillSwitchTrigger = KillSwitchTrigger.MANUAL,
        now: datetime | None = None,
    ) -> KillSwitch:
        access.require(Permission.KILL_SWITCH)
        return self._activate(
            workspace_id=access.workspace.workspace_id,
            account_id=account_id,
            actor_id=access.user_id,
            reason=reason,
            action=f"kill_switch.activate.{trigger.value}",
            now=now or datetime.now(UTC),
        )

    def trip_automatically(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID | None,
        trigger: KillSwitchTrigger,
        reason: str,
        now: datetime | None = None,
    ) -> KillSwitch:
        if trigger is KillSwitchTrigger.MANUAL:
            raise PortError(ErrorCode.INVALID_INPUT, "automatic trip requires a system trigger")
        return self._activate(
            workspace_id=workspace_id,
            account_id=account_id,
            actor_id=UUID(int=0),
            reason=reason,
            action=f"kill_switch.trip.{trigger.value}",
            now=now or datetime.now(UTC),
        )

    def evaluate_safety_triggers(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID | None,
        daily_loss: Decimal,
        daily_loss_limit: Decimal,
        data_integrity_ok: bool,
        consecutive_rejections: int,
        rejection_limit: int,
        reconciliation_match: bool,
        now: datetime | None = None,
    ) -> KillSwitch | None:
        if (
            not isinstance(daily_loss, Decimal)
            or not isinstance(daily_loss_limit, Decimal)
            or not daily_loss.is_finite()
            or not daily_loss_limit.is_finite()
            or daily_loss < 0
            or daily_loss_limit <= 0
            or type(data_integrity_ok) is not bool
            or type(reconciliation_match) is not bool
            or type(consecutive_rejections) is not int
            or consecutive_rejections < 0
            or type(rejection_limit) is not int
            or rejection_limit < 1
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid automatic safety measurements")
        trigger: KillSwitchTrigger | None = None
        reason = ""
        if not reconciliation_match:
            trigger, reason = (
                KillSwitchTrigger.RECONCILIATION_MISMATCH,
                "broker reconciliation reported a mismatch",
            )
        elif not data_integrity_ok:
            trigger, reason = (
                KillSwitchTrigger.DATA_INTEGRITY,
                "market or execution data failed integrity checks",
            )
        elif daily_loss >= daily_loss_limit:
            trigger, reason = (
                KillSwitchTrigger.DAILY_LOSS,
                "daily loss limit reached",
            )
        elif consecutive_rejections >= rejection_limit:
            trigger, reason = (
                KillSwitchTrigger.CONSECUTIVE_REJECTIONS,
                "consecutive broker rejection limit reached",
            )
        if trigger is None:
            return None
        return self.trip_automatically(
            workspace_id=workspace_id,
            account_id=account_id,
            trigger=trigger,
            reason=reason,
            now=now,
        )

    def request_recovery(
        self,
        access: WorkspaceAccess,
        *,
        account_id: UUID | None,
        now: datetime | None = None,
    ) -> RecoveryRequest:
        access.require(Permission.KILL_SWITCH)
        state = self._states.get_state(access.workspace.workspace_id, account_id)
        if state is None or not state.active:
            raise PortError(ErrorCode.CONFLICT, "kill switch is not active")
        for existing in self._recoveries.list_pending(access.workspace.workspace_id):
            if (
                existing.account_id == account_id
                and existing.requested_by == access.user_id
                and existing.switch_revision == state.revision
            ):
                return existing
        request = RecoveryRequest(
            uuid4(),
            access.workspace.workspace_id,
            account_id,
            access.user_id,
            now or datetime.now(UTC),
            state.revision,
        )
        self._recoveries.create(request)
        self._record(
            request.workspace_id,
            access.user_id,
            "kill_switch.recovery.request",
            request.request_id,
            request.requested_at,
        )
        return request

    def approve_recovery(
        self,
        access: WorkspaceAccess,
        request_id: UUID,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> KillSwitch:
        access.require(Permission.KILL_SWITCH)
        occurred_at = now or datetime.now(UTC)
        request = self._recoveries.get_request(request_id)
        if request is None:
            raise PortError(ErrorCode.NOT_FOUND, "recovery request unavailable")
        if request.workspace_id != access.workspace.workspace_id:
            raise PortError(ErrorCode.NOT_FOUND, "recovery request unavailable")
        if request.requested_by == access.user_id:
            raise PortError(ErrorCode.REJECTED, "recovery requires an independent approver")
        current = self._states.get_state(request.workspace_id, request.account_id)
        if current is None or not current.active or request.switch_revision != current.revision:
            raise PortError(ErrorCode.CONFLICT, "kill switch is not active")
        result = self._states.approve_recovery(
            request,
            approver_id=access.user_id,
            reason=reason,
            now=occurred_at,
        )
        self._record(
            request.workspace_id,
            access.user_id,
            "kill_switch.recovery.approve",
            request.request_id,
            occurred_at,
        )
        return result

    def is_active(self, workspace_id: UUID, account_id: UUID | None = None) -> bool:
        return self._states.is_active(workspace_id, account_id)

    def state(self, access: WorkspaceAccess, account_id: UUID | None = None) -> KillSwitch | None:
        access.require(Permission.READ)
        return self._states.get_state(access.workspace.workspace_id, account_id)

    def pending_recoveries(self, access: WorkspaceAccess) -> tuple[RecoveryRequest, ...]:
        access.require(Permission.READ)
        results = self._recoveries.list_pending(access.workspace.workspace_id)
        if any(item.workspace_id != access.workspace.workspace_id for item in results):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid recovery workspace scope")
        return tuple(
            item
            for item in results
            if (state := self._states.get_state(item.workspace_id, item.account_id))
            and state.active
            and state.revision == item.switch_revision
        )

    def _activate(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID | None,
        actor_id: UUID,
        reason: str,
        action: str,
        now: datetime,
    ) -> KillSwitch:
        if now.tzinfo is None or now.utcoffset() is None:
            raise PortError(ErrorCode.INVALID_INPUT, "timezone-aware switch time required")
        current = self._states.get_state(workspace_id, account_id)
        if current is not None and current.active:
            return current
        result = self._states.set_state(
            KillSwitch(
                workspace_id,
                account_id,
                True,
                reason,
                actor_id,
                now,
                1 if current is None else current.revision + 1,
            ),
            expected_revision=None if current is None else current.revision,
        )
        self._record(workspace_id, actor_id, action, account_id, now)
        return result

    def _record(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        action: str,
        target: UUID | None,
        now: datetime,
    ) -> None:
        self._audit.append(
            AuditRecord(
                workspace_id,
                "system" if actor_id.int == 0 else "user",
                None if actor_id.int == 0 else actor_id,
                action,
                "success",
                now,
                "risk.kill_switch",
                uuid4(),
                details=AuditDetails(target_id=target, target_workspace_id=workspace_id),
            )
        )
