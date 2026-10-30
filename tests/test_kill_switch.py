from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from qtrade_application.kill_switch import (
    KillSwitchService,
    KillSwitchTrigger,
    RecoveryRequest,
)
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.kill_switch import KillSwitch
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.audit import MockAuditStore


class _States:
    def __init__(self, recoveries=None):
        self.rows = {}
        self.recoveries = recoveries

    def get_state(self, workspace_id, account_id=None):
        return self.rows.get((workspace_id, account_id))

    def is_active(self, workspace_id, account_id=None):
        return any(
            key[0] == workspace_id and row.active and key[1] in (None, account_id)
            for key, row in self.rows.items()
        )

    def approve_recovery(self, request, *, approver_id, reason, now):
        current = self.get_state(request.workspace_id, request.account_id)
        if current is None or not current.active or current.revision != request.switch_revision:
            raise PortError(ErrorCode.CONFLICT, "stale recovery")
        result = self.set_state(
            KillSwitch(
                request.workspace_id,
                request.account_id,
                False,
                reason,
                approver_id,
                now,
                current.revision + 1,
            ),
            expected_revision=current.revision,
        )
        self.recoveries.consume(request.request_id, approver_id=approver_id, now=now)
        return result

    def set_state(self, state, *, expected_revision):
        key = (state.workspace_id, state.account_id)
        current = self.rows.get(key)
        if (current.revision if current else None) != expected_revision:
            raise PortError(ErrorCode.CONFLICT, "stale revision")
        self.rows[key] = state
        return state


class _Recoveries:
    def __init__(self):
        self.rows = {}

    def create(self, request):
        self.rows[request.request_id] = request

    def list_pending(self, workspace_id):
        return tuple(item for item in self.rows.values() if item.workspace_id == workspace_id)

    def get_request(self, request_id):
        return self.rows.get(request_id)

    def consume(self, request_id, *, approver_id, now):
        request = self.rows.pop(request_id, None)
        if request is None:
            raise PortError(ErrorCode.CONFLICT, "already consumed")
        return request


def _access(role: Role, workspace_id: UUID, user_id: UUID) -> WorkspaceAccess:
    now = datetime.now(UTC)
    return WorkspaceAccess(
        Workspace(workspace_id, "Personal", "personal", None, False, False, now),
        user_id,
        role,
    )


def test_global_and_account_switch_block_and_recover_with_independent_approval():
    workspace_id, account_id = uuid4(), uuid4()
    operator, approver = uuid4(), uuid4()
    recoveries, audit = _Recoveries(), MockAuditStore()
    states = _States(recoveries)
    service = KillSwitchService(states, recoveries, audit)

    account_switch = service.activate(
        _access(Role.RISK_MANAGER, workspace_id, operator),
        account_id=account_id,
        reason="reconciliation mismatch",
    )
    assert account_switch.active
    assert service.is_active(workspace_id, account_id)
    assert not service.is_active(workspace_id, uuid4())

    request = service.request_recovery(
        _access(Role.RISK_MANAGER, workspace_id, operator), account_id=account_id
    )
    with pytest.raises(PortError, match="independent approver"):
        service.approve_recovery(
            _access(Role.RISK_MANAGER, workspace_id, operator),
            request.request_id,
            reason="review complete",
        )
    assert request.request_id in recoveries.rows
    recovered = service.approve_recovery(
        _access(Role.ADMIN, workspace_id, approver),
        request.request_id,
        reason="independent review complete",
    )
    assert not recovered.active
    assert not service.is_active(workspace_id, account_id)
    assert audit.records[-1].action == "kill_switch.recovery.approve"


def test_automatic_trigger_is_audited_as_system_and_manual_trigger_needs_access():
    workspace_id = uuid4()
    states, audit = _States(), MockAuditStore()
    service = KillSwitchService(states, _Recoveries(), audit)
    tripped = service.trip_automatically(
        workspace_id=workspace_id,
        account_id=None,
        trigger=KillSwitchTrigger.DAILY_LOSS,
        reason="daily loss limit exceeded",
    )
    assert tripped.active
    assert audit.records[-1].actor_type == "system"
    assert audit.records[-1].actor_id is None
    with pytest.raises(PortError, match="permission denied"):
        service.activate(
            _access(Role.VIEWER, workspace_id, uuid4()),
            account_id=None,
            reason="manual pause",
        )


@pytest.mark.parametrize(
    ("loss", "integrity", "rejects", "reconciled", "expected"),
    [
        (Decimal("0"), True, 0, True, None),
        (Decimal("10"), True, 0, True, "daily_loss"),
        (Decimal("0"), False, 0, True, "data_integrity"),
        (Decimal("0"), True, 3, True, "consecutive_rejections"),
        (Decimal("0"), True, 0, False, "reconciliation_mismatch"),
    ],
)
def test_automatic_trip_thresholds(loss, integrity, rejects, reconciled, expected):
    workspace_id = uuid4()
    states, audit = _States(), MockAuditStore()
    service = KillSwitchService(states, _Recoveries(), audit)
    result = service.evaluate_safety_triggers(
        workspace_id=workspace_id,
        account_id=None,
        daily_loss=loss,
        daily_loss_limit=Decimal("10"),
        data_integrity_ok=integrity,
        consecutive_rejections=rejects,
        rejection_limit=3,
        reconciliation_match=reconciled,
    )
    assert (result is not None) == (expected is not None)
    if expected:
        assert audit.records[-1].action == f"kill_switch.trip.{expected}"


def test_recovery_request_workspace_scope_is_checked_before_consumption():
    workspace_id = uuid4()
    states, recoveries = _States(), _Recoveries()
    service = KillSwitchService(states, recoveries, MockAuditStore())
    service.activate(
        _access(Role.RISK_MANAGER, workspace_id, uuid4()),
        account_id=None,
        reason="system event",
    )
    request = service.request_recovery(
        _access(Role.RISK_MANAGER, workspace_id, uuid4()), account_id=None
    )
    with pytest.raises(PortError, match="unavailable"):
        service.approve_recovery(
            _access(Role.ADMIN, uuid4(), uuid4()),
            request.request_id,
            reason="should not consume",
        )
    assert isinstance(recoveries.rows[request.request_id], RecoveryRequest)
