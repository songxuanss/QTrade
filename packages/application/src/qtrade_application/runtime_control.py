"""Audited runtime pause, resume, stop and close controls."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from qtrade_domain.audit_port import (
    AuditContext,
    AuditDetails,
    AuditRecord,
    AuditStorePort,
    contextualize,
)
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.runtime_control import (
    ExecutionRuntime,
    ExecutionRuntimeStore,
    RuntimeOrderControlPort,
)

RuntimeAction = Literal["pause", "resume", "stop", "cancel_orders", "close_positions"]


class UnavailableRuntimeOrderControl:
    """Fail closed until a broker-backed cancel/close adapter is composed."""

    def cancel_open_orders(self, workspace_id: UUID, runtime_id: UUID) -> int:
        del workspace_id, runtime_id
        raise PortError(ErrorCode.UNAVAILABLE, "broker order control is not configured")

    def close_positions(self, workspace_id: UUID, runtime_id: UUID) -> int:
        del workspace_id, runtime_id
        raise PortError(ErrorCode.UNAVAILABLE, "broker position control is not configured")


@dataclass(frozen=True)
class RuntimeControlResult:
    action: RuntimeAction
    runtime: ExecutionRuntime
    affected_count: int


class RuntimeControlService:
    def __init__(
        self,
        runtimes: ExecutionRuntimeStore,
        orders: RuntimeOrderControlPort,
        audit: AuditStorePort,
    ):
        self._runtimes, self._orders, self._audit = runtimes, orders, audit

    @property
    def order_control_available(self) -> bool:
        """True only when a broker-backed cancel/close adapter is composed."""
        return not isinstance(self._orders, UnavailableRuntimeOrderControl)

    def list(self, access: WorkspaceAccess) -> tuple[ExecutionRuntime, ...]:
        access.require(Permission.READ)
        return self._runtimes.list(access.workspace.workspace_id, limit=100)

    def control(
        self,
        access: WorkspaceAccess,
        runtime_id: UUID,
        action: RuntimeAction,
        *,
        confirmation: str | None,
        now: datetime | None = None,
        audit_context: AuditContext | None = None,
    ) -> RuntimeControlResult:
        runtime = self._runtimes.get(access.workspace.workspace_id, runtime_id)
        if runtime is None:
            raise PortError(ErrorCode.NOT_FOUND, "runtime unavailable")
        access.require(Permission.RUNTIME_CONTROL, object_workspace_id=runtime.workspace_id)
        if action in {"pause", "resume", "stop"}:
            access.require(
                Permission.PAPER_START if runtime.mode == "paper" else Permission.LIVE_START,
                object_workspace_id=runtime.workspace_id,
            )
        if action in {"stop", "cancel_orders", "close_positions"} and confirmation != (
            "CONFIRM " + action.upper()
        ):
            raise PortError(ErrorCode.REJECTED, "explicit runtime control confirmation required")
        if action == "close_positions":
            access.require(Permission.POSITION_CLOSE, object_workspace_id=runtime.workspace_id)
        if (
            action == "cancel_orders"
            and runtime.mode == "live"
            and not ({Permission.ORDER_SUBMIT, Permission.KILL_SWITCH} & access.permissions)
        ):
            raise PortError(ErrorCode.REJECTED, "workspace permission denied")
        if runtime.state == "stopped":
            raise PortError(ErrorCode.CONFLICT, "stopped runtime cannot be controlled")

        occurred_at = now or datetime.now(UTC)
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise PortError(ErrorCode.INVALID_INPUT, "timezone-aware control time required")
        record = contextualize(
            AuditRecord(
                runtime.workspace_id,
                "user",
                access.user_id,
                f"runtime.{action}",
                "success",
                occurred_at,
                "runtime.control",
                uuid4(),
                details=AuditDetails(
                    target_id=runtime.runtime_id,
                    target_workspace_id=runtime.workspace_id,
                ),
            ),
            audit_context,
        )
        affected_count = 0
        next_runtime = runtime
        if action == "pause":
            next_runtime = self._transition(runtime, "paused", occurred_at, record)
        elif action == "resume":
            next_runtime = self._transition(runtime, "running", occurred_at, record)
        elif action == "stop":
            affected_count = self._orders.cancel_open_orders(
                runtime.workspace_id, runtime.runtime_id
            )
            next_runtime = self._transition(runtime, "stopped", occurred_at, record)
        elif action == "cancel_orders":
            affected_count = self._orders.cancel_open_orders(
                runtime.workspace_id, runtime.runtime_id
            )
        else:
            affected_count = self._orders.close_positions(runtime.workspace_id, runtime.runtime_id)

        if action in {"cancel_orders", "close_positions"}:
            self._audit.append(record)
        return RuntimeControlResult(action, next_runtime, affected_count)

    def _transition(
        self,
        runtime: ExecutionRuntime,
        state: Literal["running", "paused", "stopped"],
        now: datetime,
        audit: AuditRecord,
    ) -> ExecutionRuntime:
        allowed = {
            ("running", "paused"),
            ("paused", "running"),
            ("running", "stopped"),
            ("paused", "stopped"),
        }
        if (runtime.state, state) not in allowed:
            raise PortError(ErrorCode.CONFLICT, "illegal runtime control transition")
        return self._runtimes.transition(
            runtime,
            expected_revision=runtime.revision,
            state=state,
            updated_at=now,
            audit=audit,
        )
