"""Strategy Version qualification, state transitions, launch and approval use cases."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from qtrade_application.strategies import StrategyService
from qtrade_application.strategy_validation import validate_strategy_source
from qtrade_contracts import StrategyVersion
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.strategies import (
    Strategy,
    StrategyLifecyclePort,
    StrategyQualification,
    StrategyQualificationPort,
    StrategyRuntimePort,
    StrategyStateTransition,
    StrategyVersionApproval,
    StrategyVersionLifecycle,
    StrategyVersionState,
)

_ALLOWED_TRANSITIONS = {
    StrategyVersionState.DRAFT: {StrategyVersionState.VALIDATED, StrategyVersionState.ARCHIVED},
    StrategyVersionState.VALIDATED: {
        StrategyVersionState.BACKTESTED,
        StrategyVersionState.ARCHIVED,
    },
    StrategyVersionState.BACKTESTED: {
        StrategyVersionState.PAPER,
        StrategyVersionState.ARCHIVED,
    },
    StrategyVersionState.PAPER: {
        StrategyVersionState.APPROVED,
        StrategyVersionState.ARCHIVED,
    },
    StrategyVersionState.APPROVED: {
        StrategyVersionState.LIVE,
        StrategyVersionState.ARCHIVED,
    },
    StrategyVersionState.LIVE: {StrategyVersionState.PAUSED},
    StrategyVersionState.PAUSED: {
        StrategyVersionState.LIVE,
        StrategyVersionState.ARCHIVED,
    },
    StrategyVersionState.FAILED: {StrategyVersionState.ARCHIVED},
    StrategyVersionState.ARCHIVED: set(),
}


class StrategyLifecycleService:
    def __init__(
        self,
        strategies: StrategyService,
        lifecycle: StrategyLifecyclePort,
        qualifications: StrategyQualificationPort | None = None,
        runtime: StrategyRuntimePort | None = None,
    ) -> None:
        self._strategies = strategies
        self._lifecycle = lifecycle
        self._qualifications = qualifications
        self._runtime = runtime

    def state(
        self, access: WorkspaceAccess, strategy_id: UUID, version: int
    ) -> StrategyVersionLifecycle:
        self._require_version(access, strategy_id, version)
        return self._lifecycle.state(access.workspace.workspace_id, strategy_id, version)

    def history(
        self, access: WorkspaceAccess, strategy_id: UUID, version: int
    ) -> tuple[StrategyStateTransition, ...]:
        self._require_version(access, strategy_id, version)
        return self._lifecycle.history(access.workspace.workspace_id, strategy_id, version)

    def approval(
        self, access: WorkspaceAccess, strategy_id: UUID, version: int
    ) -> StrategyVersionApproval | None:
        self._require_version(access, strategy_id, version)
        return self._lifecycle.approval(access.workspace.workspace_id, strategy_id, version)

    def transition(
        self,
        access: WorkspaceAccess,
        strategy_id: UUID,
        version: int,
        target: StrategyVersionState,
        *,
        reason: str = "",
    ) -> StrategyVersionLifecycle:
        strategy_version = self._require_version(access, strategy_id, version)
        current = self._lifecycle.state(access.workspace.workspace_id, strategy_id, version)
        if target in {StrategyVersionState.APPROVED, StrategyVersionState.LIVE}:
            raise PortError(ErrorCode.REJECTED, "use the version approval or launch operation")
        self._require_transition_permission(access, target)
        self._check_transition(current.state, target)
        qualification = self._qualification(access, strategy_id, version)
        evidence: dict[str, object] = {}

        if target == StrategyVersionState.VALIDATED:
            code = self._strategies.read_code(access, strategy_id, version)
            try:
                dependencies = json.loads(
                    self._strategies.read_dependencies(access, strategy_id, version)
                )
            except PortError as error:
                if error.code != ErrorCode.NOT_FOUND:
                    raise
                dependencies = {}
            result = validate_strategy_source(code, dependencies)
            if not result.passed:
                raise PortError(ErrorCode.REJECTED, "strategy static validation must pass")
            evidence = {
                "code_sha256": strategy_version.code_sha256,
                "dependencies_sha256": strategy_version.dependencies_sha256,
                "static_validation": "passed",
                "ast_node_count": result.ast_node_count,
            }
        elif target == StrategyVersionState.BACKTESTED:
            if not qualification.backtest_succeeded or qualification.backtest_run_id is None:
                raise PortError(ErrorCode.REJECTED, "a successful Backtest Run is required")
            evidence = {"backtest_run_id": str(qualification.backtest_run_id)}
        elif target == StrategyVersionState.PAPER:
            if not qualification.backtest_succeeded or qualification.backtest_run_id is None:
                raise PortError(ErrorCode.REJECTED, "a successful Backtest Run is required")
            evidence = {"backtest_run_id": str(qualification.backtest_run_id)}
        elif target == StrategyVersionState.PAUSED:
            raise PortError(
                ErrorCode.REJECTED,
                "only the execution runtime can pause a live version",
            )
        elif target == StrategyVersionState.ARCHIVED:
            evidence = {"source": "version_archive"}

        now = datetime.now(UTC)
        lifecycle = StrategyVersionLifecycle(
            current.workspace_id,
            current.strategy_id,
            current.strategy_version_id,
            current.version,
            target,
            now,
        )
        event = self._event(current, target, access.user_id, now, reason, evidence)
        return self._lifecycle.transition(current.state, lifecycle, event)

    def approve(
        self,
        access: WorkspaceAccess,
        strategy_id: UUID,
        version: int,
        *,
        reason: str,
    ) -> tuple[StrategyVersionLifecycle, StrategyVersionApproval]:
        access.require(Permission.LIVE_APPROVE)
        strategy_version = self._require_version(access, strategy_id, version)
        if access.user_id == strategy_version.created_by:
            raise PortError(ErrorCode.REJECTED, "strategy authors cannot approve their own version")
        current = self._lifecycle.state(access.workspace.workspace_id, strategy_id, version)
        target = StrategyVersionState.APPROVED
        self._check_transition(current.state, target)
        qualification = self._qualification(access, strategy_id, version)
        if not qualification.backtest_succeeded or qualification.backtest_run_id is None:
            raise PortError(ErrorCode.REJECTED, "a successful Backtest Run is required")
        if qualification.paper_observations < qualification.required_paper_observations:
            raise PortError(
                ErrorCode.REJECTED,
                "paper observation requirement is not met",
            )
        now = datetime.now(UTC)
        approval = StrategyVersionApproval(
            uuid4(),
            current.workspace_id,
            current.strategy_id,
            current.strategy_version_id,
            current.version,
            access.user_id,
            now,
            qualification.backtest_run_id,
            qualification.paper_observations,
            _reason(reason),
        )
        lifecycle = StrategyVersionLifecycle(
            current.workspace_id,
            current.strategy_id,
            current.strategy_version_id,
            current.version,
            target,
            now,
        )
        event = self._event(
            current,
            target,
            access.user_id,
            now,
            reason,
            {
                "approval_id": str(approval.approval_id),
                "backtest_run_id": str(approval.backtest_run_id),
                "paper_observations": approval.paper_observations,
            },
        )
        return self._lifecycle.approve(current.state, lifecycle, event, approval), approval

    def launch(
        self, access: WorkspaceAccess, strategy_id: UUID, version: int
    ) -> StrategyVersionLifecycle:
        access.require(Permission.LIVE_START)
        self._require_version(access, strategy_id, version)
        current = self._lifecycle.state(access.workspace.workspace_id, strategy_id, version)
        target = StrategyVersionState.LIVE
        self._check_transition(current.state, target)
        approval = self._lifecycle.approval(access.workspace.workspace_id, strategy_id, version)
        if approval is None or approval.strategy_version_id != current.strategy_version_id:
            raise PortError(ErrorCode.REJECTED, "this exact Strategy Version has no approval")
        qualification = self._qualification(access, strategy_id, version)
        if (
            not qualification.backtest_succeeded
            or qualification.backtest_run_id != approval.backtest_run_id
            or qualification.paper_observations < qualification.required_paper_observations
        ):
            raise PortError(
                ErrorCode.REJECTED, "approved qualification evidence is no longer valid"
            )
        if self._runtime is None or not self._runtime.is_live(
            current.workspace_id, current.strategy_id, current.strategy_version_id
        ):
            raise PortError(
                ErrorCode.REJECTED,
                "live execution runtime is not active for this version",
            )
        now = datetime.now(UTC)
        lifecycle = StrategyVersionLifecycle(
            current.workspace_id,
            current.strategy_id,
            current.strategy_version_id,
            current.version,
            target,
            now,
        )
        event = self._event(
            current,
            target,
            access.user_id,
            now,
            "approved version launch",
            {
                "approval_id": str(approval.approval_id),
            },
        )
        return self._lifecycle.transition(current.state, lifecycle, event)

    def archive_strategy(self, access: WorkspaceAccess, strategy_id: UUID) -> Strategy:
        access.require(Permission.STRATEGY_ARCHIVE)
        self._strategies.get(access, strategy_id)
        return self._lifecycle.archive_strategy(
            access.workspace.workspace_id, strategy_id, access.user_id, datetime.now(UTC)
        )

    def _qualification(
        self, access: WorkspaceAccess, strategy_id: UUID, version: int
    ) -> StrategyQualification:
        if self._qualifications is None:
            return StrategyQualification()
        return self._qualifications.for_version(access.workspace.workspace_id, strategy_id, version)

    def _require_version(
        self, access: WorkspaceAccess, strategy_id: UUID, version: int
    ) -> StrategyVersion:
        if type(version) is not int or version < 1:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy version")
        rows = self._strategies.versions(access, strategy_id)
        result = next((item for item in rows if item.version == version), None)
        if result is None:
            raise PortError(ErrorCode.NOT_FOUND, "strategy version unavailable")
        return result

    @staticmethod
    def _check_transition(current: StrategyVersionState, target: StrategyVersionState) -> None:
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise PortError(
                ErrorCode.REJECTED, f"invalid strategy state transition: {current} → {target}"
            )

    @staticmethod
    def _require_transition_permission(
        access: WorkspaceAccess, target: StrategyVersionState
    ) -> None:
        permission = {
            StrategyVersionState.VALIDATED: Permission.STRATEGY_WRITE,
            StrategyVersionState.BACKTESTED: Permission.STRATEGY_WRITE,
            StrategyVersionState.PAPER: Permission.PAPER_START,
            StrategyVersionState.ARCHIVED: Permission.STRATEGY_ARCHIVE,
        }.get(target)
        if permission is None:
            raise PortError(ErrorCode.REJECTED, "state transition is system controlled")
        access.require(permission)

    @staticmethod
    def _event(
        current: StrategyVersionLifecycle,
        target: StrategyVersionState,
        actor_id: UUID,
        now: datetime,
        reason: str,
        evidence: dict[str, object],
    ) -> StrategyStateTransition:
        return StrategyStateTransition(
            uuid4(),
            current.workspace_id,
            current.strategy_id,
            current.strategy_version_id,
            current.version,
            current.state,
            target,
            actor_id,
            now,
            _reason(reason),
            json.dumps(evidence, sort_keys=True, separators=(",", ":")),
        )


def _reason(value: str) -> str:
    if not isinstance(value, str) or len(value) > 512 or any(char in value for char in "\r\n"):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid lifecycle reason")
    return value.strip()
