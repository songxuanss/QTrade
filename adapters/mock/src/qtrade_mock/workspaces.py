"""Deterministic workspace adapter with the same live grants and admin invariants."""

from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID, uuid4

from qtrade_domain.audit_port import AuditContext, AuditDetails, AuditRecord, contextualize
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.identity import Membership, Role, User, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.audit import MockAuditStore


class MockWorkspaceStore:
    def __init__(
        self,
        users: tuple[User, ...],
        workspaces: tuple[Workspace, ...],
        memberships: tuple[Membership, ...],
    ):
        self.audit = MockAuditStore()
        self.users = {user.user_id: user for user in users}
        self.workspaces = {workspace.workspace_id: workspace for workspace in workspaces}
        self.memberships = {(member.workspace_id, member.user_id): member for member in memberships}
        self._lock = RLock()

    def access(self, user_id: UUID, workspace_id: UUID) -> WorkspaceAccess | None:
        with self._lock:
            user, workspace = self.users.get(user_id), self.workspaces.get(workspace_id)
            member = self.memberships.get((workspace_id, user_id))
            if (
                user is None
                or user.disabled
                or workspace is None
                or workspace.archived
                or member is None
                or not member.active
            ):
                return None
            return WorkspaceAccess(workspace, user_id, member.role)

    def accessible(self, user_id: UUID) -> tuple[WorkspaceAccess, ...]:
        with self._lock:
            return tuple(
                access
                for workspace in sorted(
                    self.workspaces.values(), key=lambda w: (w.created_at, w.workspace_id)
                )
                if (access := self.access(user_id, workspace.workspace_id)) is not None
            )

    def _admin(self, actor: UUID, workspace: UUID) -> None:
        access = self.access(actor, workspace)
        if access is None:
            raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
        access.require(Permission.MEMBER_MANAGE)

    def members(self, actor_id: UUID, workspace_id: UUID) -> tuple[Membership, ...]:
        with self._lock:
            self._admin(actor_id, workspace_id)
            return tuple(
                sorted(
                    (
                        member
                        for member in self.memberships.values()
                        if member.workspace_id == workspace_id
                    ),
                    key=lambda m: m.user_id,
                )
            )

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
        with self._lock:
            self._admin(actor_id, workspace_id)
            user = self.users.get(user_id)
            if user is None or (active and user.disabled):
                raise PortError(ErrorCode.NOT_FOUND, "membership target unavailable")
            current = self.memberships.get((workspace_id, user_id))
            if (
                current
                and current.role == Role.ADMIN
                and current.active
                and (not active or role != Role.ADMIN)
            ):
                admins = [
                    member
                    for member in self.memberships.values()
                    if member.workspace_id == workspace_id
                    and member.role == Role.ADMIN
                    and member.active
                    and not self.users[member.user_id].disabled
                ]
                if len(admins) <= 1:
                    raise PortError(
                        ErrorCode.CONFLICT, "workspace requires an active administrator"
                    )
            now = datetime.now(UTC)
            member = (
                replace(current, role=role, active=active, updated_at=now)
                if current
                else Membership(workspace_id, user_id, role, active, actor_id, now, now)
            )
            self.audit.append(
                contextualize(
                    AuditRecord(
                        workspace_id,
                        "user",
                        actor_id,
                        "membership.change",
                        "success",
                        now,
                        "workspace",
                        uuid4(),
                        details=AuditDetails(
                            target_id=user_id,
                            role_before=current.role if current else None,
                            active_before=current.active if current else None,
                            role_after=role,
                            active_after=active,
                        ),
                    ),
                    audit_context,
                )
            )
            self.memberships[(workspace_id, user_id)] = member
            return member
