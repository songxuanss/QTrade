"""Concurrent in-memory quality issue repository for behavior tests."""

from collections.abc import Callable
from datetime import datetime
from threading import RLock
from uuid import UUID

from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.quality import QualityIssue, claim, close


class MockQualityIssueStore:
    def __init__(self, access_for_actor: Callable[[UUID, UUID], WorkspaceAccess | None]):
        self._access_for_actor = access_for_actor
        self._issues: dict[tuple[UUID, UUID], QualityIssue] = {}
        self._lock = RLock()

    def _authorize(self, actor_id: UUID, workspace_id: UUID) -> None:
        access = self._access_for_actor(actor_id, workspace_id)
        if access is None or access.workspace.workspace_id != workspace_id:
            raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
        access.require(Permission.DATA_MANAGE, object_workspace_id=workspace_id)

    def record_scan(self, actor_id: UUID, issues: tuple[QualityIssue, ...]) -> None:
        if not issues:
            return
        workspace_ids = {issue.workspace_id for issue in issues}
        if len(workspace_ids) != 1:
            raise PortError(ErrorCode.INVALID_INPUT, "quality scan spans workspaces")
        workspace_id = next(iter(workspace_ids))
        self._authorize(actor_id, workspace_id)
        with self._lock:
            for issue in issues:
                key = (workspace_id, issue.issue_id)
                existing = self._issues.get(key)
                if existing is not None and (
                    existing.issue_id != issue.issue_id
                    or existing.dataset_id != issue.dataset_id
                    or existing.evidence != issue.evidence
                    or existing.code != issue.code
                ):
                    raise PortError(ErrorCode.CONFLICT, "quality issue identity reused")
                if existing is None:
                    self._issues[key] = issue

    def list_issues(
        self, workspace_id: UUID, dataset_id: UUID | None = None, limit: int = 100
    ) -> tuple[QualityIssue, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid quality issue page size")
        with self._lock:
            found = sorted(
                (
                    issue
                    for (scope, _), issue in self._issues.items()
                    if scope == workspace_id
                    and (dataset_id is None or issue.dataset_id == dataset_id)
                ),
                key=lambda issue: (issue.starts_at, str(issue.issue_id)),
                reverse=True,
            )
            return tuple(found[:limit])

    def claim_issue(
        self, actor_id: UUID, workspace_id: UUID, issue_id: UUID, now: datetime
    ) -> QualityIssue:
        self._authorize(actor_id, workspace_id)
        with self._lock:
            key = (workspace_id, issue_id)
            issue = self._issues.get(key)
            if issue is None:
                raise PortError(ErrorCode.NOT_FOUND, "quality issue unavailable")
            result = claim(issue, actor_id)
            self._issues[key] = result
            return result

    def close_issue(
        self, actor_id: UUID, workspace_id: UUID, issue_id: UUID, now: datetime
    ) -> QualityIssue:
        self._authorize(actor_id, workspace_id)
        with self._lock:
            key = (workspace_id, issue_id)
            issue = self._issues.get(key)
            if issue is None:
                raise PortError(ErrorCode.NOT_FOUND, "quality issue unavailable")
            result = close(issue, actor_id, now)
            self._issues[key] = result
            return result
