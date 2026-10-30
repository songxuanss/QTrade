"""Workspace roles are explicit grants, never SSO scopes or a role hierarchy."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError


class Permission(StrEnum):
    READ = "workspace.read"
    STRATEGY_WRITE = "strategy.write"
    STRATEGY_ARCHIVE = "strategy.archive"
    BACKTEST_RUN = "backtest.run"
    PAPER_START = "paper.start"
    ORDER_SUBMIT = "order.submit"
    LIVE_START = "live.start"
    POSITION_CLOSE = "position.close"
    ALERT_RULE_WRITE = "alert_rule.write"
    ALERT_HANDLE = "alert.handle"
    RISK_CONFIGURE = "risk.configure"
    LIVE_APPROVE = "live.approve"
    RUNTIME_CONTROL = "runtime.control"
    KILL_SWITCH = "risk.kill_switch"
    MEMBER_MANAGE = "membership.manage"
    DATA_MANAGE = "data.manage"
    CREDENTIAL_MANAGE = "credential.manage"
    SYSTEM_MANAGE = "system.manage"
    AUDIT_READ = "audit.read"
    AUDIT_EXPORT = "audit.export"


_GRANTS = {
    Role.VIEWER: {Permission.READ},
    Role.ANALYST: {
        Permission.READ,
        Permission.STRATEGY_WRITE,
        Permission.BACKTEST_RUN,
        Permission.PAPER_START,
        Permission.RUNTIME_CONTROL,
        Permission.ALERT_RULE_WRITE,
        Permission.ALERT_HANDLE,
    },
    Role.TRADER: {
        Permission.READ,
        Permission.ORDER_SUBMIT,
        Permission.RUNTIME_CONTROL,
        Permission.LIVE_START,
        Permission.POSITION_CLOSE,
        Permission.ALERT_HANDLE,
    },
    Role.RISK_MANAGER: {
        Permission.READ,
        Permission.RISK_CONFIGURE,
        Permission.LIVE_APPROVE,
        Permission.KILL_SWITCH,
        Permission.ALERT_HANDLE,
        Permission.AUDIT_READ,
        Permission.AUDIT_EXPORT,
    },
    Role.ADMIN: {
        Permission.READ,
        Permission.STRATEGY_WRITE,
        Permission.BACKTEST_RUN,
        Permission.PAPER_START,
        Permission.RUNTIME_CONTROL,
        Permission.ORDER_SUBMIT,
        Permission.LIVE_START,
        Permission.POSITION_CLOSE,
        Permission.ALERT_RULE_WRITE,
        Permission.ALERT_HANDLE,
        Permission.KILL_SWITCH,
        Permission.MEMBER_MANAGE,
        Permission.DATA_MANAGE,
        Permission.CREDENTIAL_MANAGE,
        Permission.SYSTEM_MANAGE,
        Permission.STRATEGY_ARCHIVE,
        Permission.RISK_CONFIGURE,
        Permission.AUDIT_READ,
        Permission.AUDIT_EXPORT,
    },
}
ROLE_PERMISSIONS = MappingProxyType({role: frozenset(grants) for role, grants in _GRANTS.items()})


@dataclass(frozen=True)
class WorkspaceAccess:
    workspace: Workspace
    user_id: UUID
    role: Role

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]

    def require(self, permission: Permission, *, object_workspace_id: UUID | None = None) -> None:
        if self.workspace.archived or (
            object_workspace_id is not None and object_workspace_id != self.workspace.workspace_id
        ):
            raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
        if permission not in self.permissions:
            raise PortError(ErrorCode.REJECTED, "workspace permission denied")
