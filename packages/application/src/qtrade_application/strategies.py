"""Workspace-scoped Strategy use cases; code, schema and dependencies freeze per version."""

import difflib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from qtrade_contracts import StrategyVersion
from qtrade_contracts.models import canonical_object
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, ObjectStorePort, PortError
from qtrade_domain.strategies import (
    Strategy,
    StrategyPort,
    canonical_parameter_schema,
    resolve_parameters,
)


@dataclass(frozen=True, slots=True)
class StrategyVersionDiff:
    before: StrategyVersion
    after: StrategyVersion
    code_unified_diff: str
    parameter_schema_changed: bool
    dependencies_changed: bool


class StrategyService:
    def __init__(self, strategies: StrategyPort, objects: ObjectStorePort):
        self._strategies, self._objects = strategies, objects

    def list(self, access: WorkspaceAccess, limit: int = 100) -> tuple[Strategy, ...]:
        access.require(Permission.READ)
        return self._strategies.list(access.workspace.workspace_id, limit)

    def get(self, access: WorkspaceAccess, strategy_id: UUID) -> Strategy:
        access.require(Permission.READ)
        strategy = self._strategies.get(access.workspace.workspace_id, strategy_id)
        if strategy is None:
            raise PortError(ErrorCode.NOT_FOUND, "strategy unavailable")
        return strategy

    def list_with_versions(
        self, access: WorkspaceAccess, limit: int = 100
    ) -> tuple[tuple[Strategy, tuple[StrategyVersion, ...]], ...]:
        """List strategies and their versions in two reads instead of one per strategy."""
        access.require(Permission.READ)
        workspace_id = access.workspace.workspace_id
        strategies = self._strategies.list(workspace_id, limit)
        active = tuple(item.strategy_id for item in strategies if not item.archived)
        grouped: dict[UUID, list[StrategyVersion]] = {}
        for version in self._strategies.versions_for(workspace_id, active):
            grouped.setdefault(version.strategy_id, []).append(version)
        return tuple((item, tuple(grouped.get(item.strategy_id, ()))) for item in strategies)

    def versions(self, access: WorkspaceAccess, strategy_id: UUID) -> tuple[StrategyVersion, ...]:
        self.get(access, strategy_id)
        result = self._strategies.versions(access.workspace.workspace_id, strategy_id)
        if not result:
            raise PortError(ErrorCode.NOT_FOUND, "strategy unavailable")
        return result

    def create(
        self,
        access: WorkspaceAccess,
        *,
        name: str,
        description: str = "",
        code: str,
        parameter_schema_json: str,
        dependencies_json: str = "{}",
    ) -> tuple[Strategy, StrategyVersion]:
        access.require(Permission.STRATEGY_WRITE)
        now = datetime.now(UTC)
        strategy_id, version_id = uuid4(), uuid4()
        strategy = Strategy(
            strategy_id,
            access.workspace.workspace_id,
            name,
            description,
            access.user_id,
            now,
            now,
            1,
        )
        version = self._make_version(
            strategy,
            version_id,
            1,
            access.user_id,
            now,
            code,
            parameter_schema_json,
            dependencies_json,
        )
        self._strategies.create(strategy, version)
        return strategy, version

    def revise(
        self,
        access: WorkspaceAccess,
        strategy_id: UUID,
        *,
        code: str,
        parameter_schema_json: str,
        dependencies_json: str = "{}",
    ) -> StrategyVersion:
        access.require(Permission.STRATEGY_WRITE)
        current = self.get(access, strategy_id)
        if current.archived:
            raise PortError(ErrorCode.REJECTED, "archived strategy cannot be revised")
        now = datetime.now(UTC)
        next_version = current.current_version + 1
        updated = replace(current, updated_at=now, current_version=next_version)
        version = self._make_version(
            updated,
            uuid4(),
            next_version,
            access.user_id,
            now,
            code,
            parameter_schema_json,
            dependencies_json,
        )
        self._strategies.append_version(
            current.workspace_id, strategy_id, current.current_version, updated, version
        )
        return version

    def read_code(self, access: WorkspaceAccess, strategy_id: UUID, version: int) -> str:
        target = self._version(access, strategy_id, version)
        try:
            content = self._objects.get(
                target.workspace_id, target.code_object_key, expected_sha256=target.code_sha256
            )
            return content.decode("utf-8")
        except UnicodeDecodeError:
            raise PortError(ErrorCode.UNAVAILABLE, "stored strategy code is invalid") from None

    def read_dependencies(self, access: WorkspaceAccess, strategy_id: UUID, version: int) -> str:
        target = self._version(access, strategy_id, version)
        if target.dependencies_object_key is None:
            raise PortError(ErrorCode.NOT_FOUND, "dependency manifest unavailable")
        try:
            content = self._objects.get(
                target.workspace_id,
                target.dependencies_object_key,
                expected_sha256=target.dependencies_sha256,
            )
            return canonical_object(content.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PortError(
                ErrorCode.UNAVAILABLE, "stored dependency manifest is invalid"
            ) from None

    def diff(
        self, access: WorkspaceAccess, strategy_id: UUID, before: int, after: int
    ) -> StrategyVersionDiff:
        versions = {item.version: item for item in self.versions(access, strategy_id)}
        old, new = versions.get(before), versions.get(after)
        if old is None or new is None:
            raise PortError(ErrorCode.NOT_FOUND, "strategy version unavailable")
        old_code, new_code = (
            self.read_code(access, strategy_id, before),
            self.read_code(access, strategy_id, after),
        )
        unified = "".join(
            difflib.unified_diff(
                old_code.splitlines(keepends=True),
                new_code.splitlines(keepends=True),
                fromfile=f"version-{before}",
                tofile=f"version-{after}",
            )
        )
        return StrategyVersionDiff(
            old,
            new,
            unified,
            old.parameter_schema_json != new.parameter_schema_json,
            old.dependencies_sha256 != new.dependencies_sha256,
        )

    def validate_parameters(
        self,
        access: WorkspaceAccess,
        strategy_id: UUID,
        version: int,
        parameters: dict[str, object],
    ) -> dict[str, object]:
        target = self._version(access, strategy_id, version)
        return resolve_parameters(target.parameter_schema_json, parameters)

    def _version(self, access: WorkspaceAccess, strategy_id: UUID, number: int) -> StrategyVersion:
        rows = self.versions(access, strategy_id)
        result = next((item for item in rows if item.version == number), None)
        if result is None:
            raise PortError(ErrorCode.NOT_FOUND, "strategy version unavailable")
        return result

    def _make_version(
        self,
        strategy: Strategy,
        version_id: UUID,
        number: int,
        actor_id: UUID,
        created_at: datetime,
        code: str,
        parameter_schema_json: str,
        dependencies_json: str,
    ) -> StrategyVersion:
        if not isinstance(code, str) or not 1 <= len(code.encode("utf-8")) <= 1_000_000:
            raise PortError(ErrorCode.INVALID_INPUT, "strategy code size is invalid")
        code_bytes = code.encode("utf-8")
        try:
            schema_json = canonical_parameter_schema(parameter_schema_json)
            deps_json = canonical_object(dependencies_json)
            if len(deps_json) > 32768:
                raise ValueError
            dependencies = json.loads(deps_json)
            if any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in dependencies.items()
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise PortError(
                ErrorCode.INVALID_INPUT, "invalid strategy schema or dependencies"
            ) from None
        code_hash, deps_hash = (
            sha256(code_bytes).hexdigest(),
            sha256(deps_json.encode()).hexdigest(),
        )
        code_key = f"strategies/{strategy.strategy_id}/versions/{number}/source.py"
        deps_key = f"strategies/{strategy.strategy_id}/versions/{number}/dependencies.json"
        self._objects.put(
            strategy.workspace_id,
            code_key,
            code_bytes,
            content_type="text/x-python; charset=utf-8",
            expected_sha256=code_hash,
        )
        self._objects.put(
            strategy.workspace_id,
            deps_key,
            deps_json.encode(),
            content_type="application/json",
            expected_sha256=deps_hash,
        )
        return StrategyVersion(
            schema_version=1,
            workspace_id=strategy.workspace_id,
            strategy_id=strategy.strategy_id,
            strategy_version_id=version_id,
            version=number,
            created_by=actor_id,
            created_at=created_at,
            code_object_key=code_key,
            code_sha256=code_hash,
            parameter_schema_json=schema_json,
            dependencies_sha256=deps_hash,
            dependencies_object_key=deps_key,
        )
