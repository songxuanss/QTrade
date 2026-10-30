"""Workspace use cases share one object-level authorization policy."""

from uuid import UUID

from qtrade_domain.audit_port import AuditContext
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.identity import Membership, Role
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.workspace_port import WorkspaceStorePort


class WorkspaceService:
    def __init__(self, store: WorkspaceStorePort):
        self._store = store

    def accessible(self, actor_id: UUID) -> tuple[WorkspaceAccess, ...]:
        return self._store.accessible(actor_id)

    def authorize(
        self,
        actor_id: UUID,
        workspace_id: UUID,
        permission: Permission,
        *,
        object_workspace_id: UUID | None = None,
    ) -> WorkspaceAccess:
        access = self._store.access(actor_id, workspace_id)
        if (
            access is None
            or access.user_id != actor_id
            or access.workspace.workspace_id != workspace_id
        ):
            raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
        access.require(permission, object_workspace_id=object_workspace_id)
        return access

    def members(self, actor_id: UUID, workspace_id: UUID) -> tuple[Membership, ...]:
        return self._store.members(actor_id, workspace_id)

    def set_member(
        self,
        actor_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        *,
        role: Role,
        active: bool,
        audit_context: AuditContext | None = None,
    ) -> Membership:
        return self._store.set_member(
            actor_id, workspace_id, user_id, role=role, active=active, audit_context=audit_context
        )
