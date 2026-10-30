"""Authorized quality scans and issue lifecycle use cases."""

from datetime import datetime
from uuid import UUID

from qtrade_contracts import Bar
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.calendars import TradingCalendar
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.quality import (
    QualityIssue,
    QualityIssueStorePort,
    QualityPolicy,
    detect_quality_issues,
)


class DataQualityService:
    def __init__(self, store: QualityIssueStorePort, policy: QualityPolicy | None = None):
        self._store = store
        self._policy = policy or QualityPolicy()

    def scan(
        self,
        access: WorkspaceAccess,
        dataset_id: UUID,
        bars: tuple[Bar, ...],
        calendars: dict[str, TradingCalendar],
        *,
        start: datetime,
        end: datetime,
        detected_at: datetime,
    ) -> tuple[QualityIssue, ...]:
        access.require(Permission.DATA_MANAGE)
        issues = detect_quality_issues(
            access.workspace.workspace_id,
            dataset_id,
            bars,
            calendars,
            start=start,
            end=end,
            detected_at=detected_at,
            policy=self._policy,
        )
        self._store.record_scan(access.user_id, issues)
        return issues

    def list(
        self, access: WorkspaceAccess, dataset_id: UUID | None = None, limit: int = 100
    ) -> tuple[QualityIssue, ...]:
        access.require(Permission.READ)
        if dataset_id is not None and not isinstance(dataset_id, UUID):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid quality dataset")
        return self._store.list_issues(access.workspace.workspace_id, dataset_id, limit)

    def claim(self, access: WorkspaceAccess, issue_id: UUID, now: datetime) -> QualityIssue:
        access.require(Permission.DATA_MANAGE)
        return self._store.claim_issue(access.user_id, access.workspace.workspace_id, issue_id, now)

    def close(self, access: WorkspaceAccess, issue_id: UUID, now: datetime) -> QualityIssue:
        access.require(Permission.DATA_MANAGE)
        return self._store.close_issue(access.user_id, access.workspace.workspace_id, issue_id, now)
