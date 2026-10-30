from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from qtrade_application.runtime_control import RuntimeControlService
from qtrade_domain.audit_port import AuditContext
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.runtime_control import ExecutionRuntime
from qtrade_mock.audit import MockAuditStore


class _Runtimes:
    def __init__(self, runtime, audit=None):
        self.rows = {runtime.runtime_id: runtime}
        self.audit = audit or MockAuditStore()

    def get(self, workspace_id, runtime_id):
        runtime = self.rows.get(runtime_id)
        return runtime if runtime and runtime.workspace_id == workspace_id else None

    def transition(self, runtime, *, expected_revision, state, updated_at, audit):
        current = self.rows[runtime.runtime_id]
        if current.revision != expected_revision:
            raise PortError(ErrorCode.CONFLICT, "runtime revision changed")
        updated = replace(
            current, state=state, revision=current.revision + 1, updated_at=updated_at
        )
        self.rows[runtime.runtime_id] = updated
        self.audit.append(audit)
        return updated


class _Orders:
    def __init__(self):
        self.cancelled = 0
        self.closed = 0

    def cancel_open_orders(self, _workspace_id, _runtime_id):
        self.cancelled += 1
        return 2

    def close_positions(self, _workspace_id, _runtime_id):
        self.closed += 1
        return 1


def _setup(role: Role, mode: str = "paper"):
    now = datetime.now(UTC)
    workspace = Workspace(uuid4(), "Personal", "personal", None, False, False, now)
    access = WorkspaceAccess(workspace, uuid4(), role)
    runtime = ExecutionRuntime(
        workspace.workspace_id,
        uuid4(),
        uuid4(),
        uuid4(),
        mode,
        "running",
        1,
        now,
    )
    orders, audit = _Orders(), MockAuditStore()
    runtimes = _Runtimes(runtime, audit)
    service = RuntimeControlService(runtimes, orders, audit)
    return now, access, runtime, runtimes, orders, audit, service


def test_runtime_pause_resume_and_stop_are_audited_and_stop_cancels_orders():
    now, access, runtime, runtimes, orders, audit, service = _setup(Role.ANALYST)
    paused = service.control(access, runtime.runtime_id, "pause", confirmation=None, now=now)
    assert paused.runtime.state == "paused"
    resumed = service.control(access, runtime.runtime_id, "resume", confirmation=None, now=now)
    assert resumed.runtime.state == "running"
    stopped = service.control(
        access,
        runtime.runtime_id,
        "stop",
        confirmation="CONFIRM STOP",
        now=now,
        audit_context=AuditContext("api.test"),
    )
    assert stopped.runtime.state == "stopped"
    assert stopped.affected_count == 2
    assert orders.cancelled == 1
    assert len(audit.records) == 3
    assert runtimes.get(access.workspace.workspace_id, runtime.runtime_id).revision == 4
    assert audit.records[-1].action == "runtime.stop"


def test_bulk_control_requires_double_confirmation_before_side_effect():
    now, access, runtime, _, orders, audit, service = _setup(Role.ANALYST)
    with pytest.raises(PortError) as error:
        service.control(access, runtime.runtime_id, "stop", confirmation="stop", now=now)
    assert error.value.code == ErrorCode.REJECTED
    assert orders.cancelled == 0
    assert audit.records == []


def test_runtime_controls_enforce_mode_and_position_permissions():
    now, analyst, runtime, _, orders, _, service = _setup(Role.ANALYST)
    live = replace(runtime, mode="live")
    live_store = _Runtimes(live)
    live_service = RuntimeControlService(live_store, orders, MockAuditStore())
    with pytest.raises(PortError) as live_error:
        live_service.control(analyst, live.runtime_id, "pause", confirmation=None, now=now)
    assert live_error.value.code == ErrorCode.REJECTED
    with pytest.raises(PortError) as close_error:
        service.control(
            analyst,
            runtime.runtime_id,
            "close_positions",
            confirmation="CONFIRM CLOSE_POSITIONS",
            now=now,
        )
    assert close_error.value.code == ErrorCode.REJECTED
    assert orders.closed == 0


def test_trader_can_cancel_live_orders_with_explicit_confirmation():
    now, access, runtime, _, orders, _, _ = _setup(Role.TRADER, "live")
    service = RuntimeControlService(_Runtimes(runtime), orders, MockAuditStore())
    result = service.control(
        access,
        runtime.runtime_id,
        "cancel_orders",
        confirmation="CONFIRM CANCEL_ORDERS",
        now=now,
    )
    assert result.affected_count == 2
    assert orders.cancelled == 1
