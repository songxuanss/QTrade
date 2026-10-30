"""Workspace-scoped runtime control state and adapter boundaries."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from qtrade_domain.audit_port import AuditRecord
from qtrade_domain.ports import ErrorCode, PortError

RuntimeMode = Literal["paper", "live"]
RuntimeState = Literal["running", "paused", "stopped"]


@dataclass(frozen=True)
class ExecutionRuntime:
    workspace_id: UUID
    runtime_id: UUID
    account_id: UUID
    strategy_version_id: UUID
    mode: RuntimeMode
    state: RuntimeState
    revision: int
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(value, UUID)
                for value in (
                    self.workspace_id,
                    self.runtime_id,
                    self.account_id,
                    self.strategy_version_id,
                )
            )
            or self.mode not in {"paper", "live"}
            or self.state not in {"running", "paused", "stopped"}
            or type(self.revision) is not int
            or self.revision < 1
            or not isinstance(self.updated_at, datetime)
            or self.updated_at.utcoffset() is None
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid execution runtime")


class ExecutionRuntimeStore(Protocol):
    def get(self, workspace_id: UUID, runtime_id: UUID) -> ExecutionRuntime | None: ...

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[ExecutionRuntime, ...]: ...

    def transition(
        self,
        runtime: ExecutionRuntime,
        *,
        expected_revision: int,
        state: RuntimeState,
        updated_at: datetime,
        audit: AuditRecord,
    ) -> ExecutionRuntime: ...


class RuntimeOrderControlPort(Protocol):
    def cancel_open_orders(self, workspace_id: UUID, runtime_id: UUID) -> int: ...

    def close_positions(self, workspace_id: UUID, runtime_id: UUID) -> int: ...
