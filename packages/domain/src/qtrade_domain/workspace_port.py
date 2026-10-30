"""Identity-context access queries and transactional membership administration."""

from typing import Protocol
from uuid import UUID

from qtrade_domain.audit_port import AuditContext
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Membership, Role


class WorkspaceStorePort(Protocol):
    def accessible(self, user_id: UUID) -> tuple[WorkspaceAccess, ...]: ...
    def access(self, user_id: UUID, workspace_id: UUID) -> WorkspaceAccess | None: ...
    def members(self, actor_id: UUID, workspace_id: UUID) -> tuple[Membership, ...]: ...
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
        """Recheck authority and prevent removing the last admin in the write transaction."""
        ...
