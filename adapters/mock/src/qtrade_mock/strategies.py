"""Thread-safe in-memory strategy catalog and immutable revision adapter."""

from dataclasses import replace
from datetime import datetime
from threading import RLock
from uuid import UUID, uuid4

from qtrade_contracts import StrategyVersion
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.strategies import (
    Strategy,
    StrategyQualification,
    StrategyStateTransition,
    StrategyVersionApproval,
    StrategyVersionLifecycle,
    StrategyVersionState,
)


class MockStrategyStore:
    def __init__(self) -> None:
        self._strategies: dict[tuple[UUID, UUID], Strategy] = {}
        self._versions: dict[tuple[UUID, UUID], list[StrategyVersion]] = {}
        self._states: dict[tuple[UUID, UUID, int], StrategyVersionLifecycle] = {}
        self._transitions: dict[tuple[UUID, UUID, int], list[StrategyStateTransition]] = {}
        self._approvals: dict[tuple[UUID, UUID, int], StrategyVersionApproval] = {}
        self._lock = RLock()

    def clear(self) -> None:
        """Reset fixture memory between isolated browser scenarios."""
        with self._lock:
            self._strategies.clear()
            self._versions.clear()
            self._states.clear()
            self._transitions.clear()
            self._approvals.clear()

    def remove_for_fixture(self, workspace_id: UUID, strategy_id: UUID) -> None:
        with self._lock:
            key = (workspace_id, strategy_id)
            self._strategies.pop(key, None)
            self._versions.pop(key, None)
            for mapping in (self._states, self._transitions, self._approvals):
                for item_key in tuple(mapping):
                    if item_key[:2] == key:
                        mapping.pop(item_key, None)

    def create(self, strategy: Strategy, version: StrategyVersion) -> None:
        key = (strategy.workspace_id, strategy.strategy_id)
        if (
            version.strategy_id != strategy.strategy_id
            or version.workspace_id != strategy.workspace_id
            or version.version != 1
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid initial strategy version")
        with self._lock:
            if key in self._strategies:
                raise PortError(ErrorCode.CONFLICT, "strategy already exists")
            self._strategies[key] = strategy
            self._versions[key] = [version]

    def get(self, workspace_id: UUID, strategy_id: UUID) -> Strategy | None:
        with self._lock:
            return self._strategies.get((workspace_id, strategy_id))

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[Strategy, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy page size")
        with self._lock:
            rows = [item for (scope, _), item in self._strategies.items() if scope == workspace_id]
            return tuple(
                sorted(
                    rows, key=lambda item: (item.updated_at, str(item.strategy_id)), reverse=True
                )[:limit]
            )

    def versions(self, workspace_id: UUID, strategy_id: UUID) -> tuple[StrategyVersion, ...]:
        with self._lock:
            return tuple(self._versions.get((workspace_id, strategy_id), ()))

    def append_version(
        self,
        workspace_id: UUID,
        strategy_id: UUID,
        expected_version: int,
        strategy: Strategy,
        version: StrategyVersion,
    ) -> None:
        key = (workspace_id, strategy_id)
        with self._lock:
            current = self._strategies.get(key)
            rows = self._versions.get(key)
            if current is None or rows is None:
                raise PortError(ErrorCode.NOT_FOUND, "strategy unavailable")
            if current.current_version != expected_version:
                raise PortError(ErrorCode.CONFLICT, "strategy version changed")
            if (strategy.strategy_id, strategy.workspace_id, strategy.current_version) != (
                strategy_id,
                workspace_id,
                expected_version + 1,
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy root version")
            if (
                version.strategy_id != strategy_id
                or version.workspace_id != workspace_id
                or version.version != expected_version + 1
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy version")
            rows.append(version)
            self._strategies[key] = strategy

    def state(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> StrategyVersionLifecycle:
        with self._lock:
            key = (workspace_id, strategy_id, version)
            row = self._states.get(key)
            if row is not None:
                return row
            versions = self._versions.get((workspace_id, strategy_id), ())
            target = next((item for item in versions if item.version == version), None)
            if target is None:
                raise PortError(ErrorCode.NOT_FOUND, "strategy version unavailable")
            return StrategyVersionLifecycle(
                workspace_id,
                strategy_id,
                target.strategy_version_id,
                version,
                StrategyVersionState.DRAFT,
                target.created_at,
            )

    def history(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> tuple[StrategyStateTransition, ...]:
        self.state(workspace_id, strategy_id, version)
        with self._lock:
            return tuple(self._transitions.get((workspace_id, strategy_id, version), ()))

    def approval(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> StrategyVersionApproval | None:
        self.state(workspace_id, strategy_id, version)
        with self._lock:
            return self._approvals.get((workspace_id, strategy_id, version))

    def transition(
        self,
        expected_state: StrategyVersionState,
        lifecycle: StrategyVersionLifecycle,
        transition: StrategyStateTransition,
    ) -> StrategyVersionLifecycle:
        key = (lifecycle.workspace_id, lifecycle.strategy_id, lifecycle.version)
        with self._lock:
            current = self.state(*key)
            if current.state != expected_state or transition.from_state != expected_state:
                raise PortError(ErrorCode.CONFLICT, "strategy state changed")
            if (transition.to_state, transition.strategy_version_id) != (
                lifecycle.state,
                lifecycle.strategy_version_id,
            ) or (
                transition.workspace_id,
                transition.strategy_id,
                transition.version,
            ) != key:
                raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy transition")
            self._states[key] = lifecycle
            self._transitions.setdefault(key, []).append(transition)
            return lifecycle

    def approve(
        self,
        expected_state: StrategyVersionState,
        lifecycle: StrategyVersionLifecycle,
        transition: StrategyStateTransition,
        approval: StrategyVersionApproval,
    ) -> StrategyVersionLifecycle:
        key = (lifecycle.workspace_id, lifecycle.strategy_id, lifecycle.version)
        with self._lock:
            current = self.state(*key)
            if current.state != expected_state or key in self._approvals:
                raise PortError(ErrorCode.CONFLICT, "strategy approval state changed")
            if (
                transition.from_state != expected_state
                or transition.to_state != StrategyVersionState.APPROVED
                or approval.strategy_version_id != lifecycle.strategy_version_id
                or (transition.workspace_id, transition.strategy_id, transition.version) != key
                or (approval.workspace_id, approval.strategy_id, approval.version) != key
            ):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy approval")
            self._states[key] = lifecycle
            self._transitions.setdefault(key, []).append(transition)
            self._approvals[key] = approval
            return lifecycle

    def archive_strategy(
        self, workspace_id: UUID, strategy_id: UUID, actor_id: UUID, now: datetime
    ) -> Strategy:
        key = (workspace_id, strategy_id)
        with self._lock:
            strategy = self._strategies.get(key)
            versions = self._versions.get(key)
            if strategy is None or versions is None:
                raise PortError(ErrorCode.NOT_FOUND, "strategy unavailable")
            states = [self.state(workspace_id, strategy_id, item.version) for item in versions]
            if any(item.state == StrategyVersionState.LIVE for item in states):
                raise PortError(ErrorCode.REJECTED, "pause live versions before archive")
            for current in states:
                if current.state == StrategyVersionState.ARCHIVED:
                    continue
                event = StrategyStateTransition(
                    uuid4(),
                    workspace_id,
                    strategy_id,
                    current.strategy_version_id,
                    current.version,
                    current.state,
                    StrategyVersionState.ARCHIVED,
                    actor_id,
                    now,
                    "strategy archive",
                    '{"source":"strategy_archive"}',
                )
                archived = replace(current, state=StrategyVersionState.ARCHIVED, updated_at=now)
                lifecycle_key = (workspace_id, strategy_id, current.version)
                self._states[lifecycle_key] = archived
                self._transitions.setdefault(lifecycle_key, []).append(event)
            archived_strategy = replace(strategy, archived=True, updated_at=now)
            self._strategies[key] = archived_strategy
            return archived_strategy


class MockStrategyQualificationStore:
    def __init__(self) -> None:
        self._facts: dict[tuple[UUID, UUID, int], StrategyQualification] = {}

    def clear(self) -> None:
        self._facts.clear()

    def remove_for_fixture(self, workspace_id: UUID, strategy_id: UUID) -> None:
        for key in tuple(self._facts):
            if key[:2] == (workspace_id, strategy_id):
                self._facts.pop(key, None)

    def set_for_version(
        self,
        workspace_id: UUID,
        strategy_id: UUID,
        version: int,
        facts: StrategyQualification,
    ) -> None:
        self._facts[(workspace_id, strategy_id, version)] = facts

    def for_version(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> StrategyQualification:
        return self._facts.get((workspace_id, strategy_id, version), StrategyQualification())
