"""Workspace-scoped risk decision query use cases."""

from datetime import datetime
from uuid import UUID

from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.risk_limits import RiskLimit, RiskLimitStore
from qtrade_domain.risk_records import RiskDecisionRecord, RiskDecisionStore


class RiskService:
    def __init__(self, decisions: RiskDecisionStore, limits: RiskLimitStore | None = None):
        self._decisions = decisions
        self._limits = limits

    def list_limits(self, access: WorkspaceAccess) -> tuple[RiskLimit, ...]:
        access.require(Permission.READ)
        if self._limits is None:
            raise PortError(ErrorCode.UNAVAILABLE, "risk limits unavailable")
        return self._limits.list(access.workspace.workspace_id)

    def record(self, record: RiskDecisionRecord) -> RiskDecisionRecord:
        """Persist a decision from a trusted runtime after verifying identity fields."""
        return self._decisions.record(record)

    def decision_for_intent(self, workspace_id: UUID, intent_id: UUID) -> RiskDecisionRecord | None:
        return self._decisions.get_for_intent(workspace_id, intent_id)

    def list_decisions(
        self,
        access: WorkspaceAccess,
        *,
        outcome: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> tuple[RiskDecisionRecord, ...]:
        access.require(Permission.READ)
        if outcome not in {None, "allow", "deny", "require_approval"}:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid risk outcome filter")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid risk decision limit")
        if since is not None and (since.tzinfo is None or since.utcoffset() is None):
            raise PortError(ErrorCode.INVALID_INPUT, "timezone-aware time required")
        return self._decisions.list(
            access.workspace.workspace_id, outcome=outcome, since=since, limit=limit
        )
