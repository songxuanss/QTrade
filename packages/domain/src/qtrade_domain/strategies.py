"""Strategy lifecycle root and immutable-version persistence seam."""

import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from qtrade_contracts import StrategyVersion
from qtrade_contracts.models import canonical_object
from qtrade_domain.ports import ErrorCode, PortError

_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "title", "description"}
_PROPERTY_KEYS = {
    "type",
    "default",
    "minimum",
    "maximum",
    "enum",
    "minLength",
    "maxLength",
    "title",
    "description",
}


def _finite_number(value: object) -> bool:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def canonical_parameter_schema(value: str) -> str:
    """Accept the intentionally small, deterministic Strategy parameter schema subset."""
    try:
        canonical = canonical_object(value)
        schema = json.loads(canonical)
        if set(schema) - _SCHEMA_KEYS or schema.get("type") != "object":
            raise ValueError
        if any(
            key in schema and not isinstance(schema[key], str) for key in ("title", "description")
        ):
            raise ValueError
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if (
            not isinstance(properties, dict)
            or not isinstance(required, list)
            or any(not isinstance(key, str) for key in required)
            or set(required) - set(properties)
            or schema.get("additionalProperties", False) is not False
        ):
            raise ValueError
        schema["additionalProperties"] = False
        for name, rule in properties.items():
            if (
                not isinstance(name, str)
                or not isinstance(rule, dict)
                or set(rule) - _PROPERTY_KEYS
            ):
                raise ValueError
            if rule.get("type") not in {"string", "integer", "number", "boolean"}:
                raise ValueError
            if rule["type"] == "string" and any(key in rule for key in ("minimum", "maximum")):
                raise ValueError
            if rule["type"] in {"integer", "number"} and any(
                key in rule for key in ("minLength", "maxLength")
            ):
                raise ValueError
            if any(key in rule and not _finite_number(rule[key]) for key in ("minimum", "maximum")):
                raise ValueError
            if "minimum" in rule and "maximum" in rule and rule["minimum"] > rule["maximum"]:
                raise ValueError
            if any(
                key in rule and (type(rule[key]) is not int or rule[key] < 0)
                for key in ("minLength", "maxLength")
            ):
                raise ValueError
            if (
                "minLength" in rule
                and "maxLength" in rule
                and rule["minLength"] > rule["maxLength"]
            ):
                raise ValueError
            if "default" in rule:
                _validate_parameter(name, rule, rule["default"])
            if "enum" in rule:
                values = rule["enum"]
                if not isinstance(values, list) or not values:
                    raise ValueError
                unrestricted = {key: item for key, item in rule.items() if key != "enum"}
                for item in values:
                    _validate_parameter(name, unrestricted, item)
            if any(
                key in rule and not isinstance(rule[key], str) for key in ("title", "description")
            ):
                raise ValueError
        return canonical_object(json.dumps(schema))
    except (KeyError, TypeError, ValueError):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy parameter schema") from None


def resolve_parameters(schema_json: str, parameters: dict[str, object]) -> dict[str, object]:
    schema = json.loads(canonical_parameter_schema(schema_json))
    resolved = dict(parameters)
    for name, rule in schema.get("properties", {}).items():
        if name not in resolved and "default" in rule:
            resolved[name] = rule["default"]
    if set(resolved) - set(schema["properties"]):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy parameter: unknown property")
    missing = set(schema.get("required", ())) - set(resolved)
    if missing:
        raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy parameter: required property")
    for name, value in resolved.items():
        _validate_parameter(name, schema["properties"][name], value)
    return resolved


def _validate_parameter(name: str, rule: dict[str, object], value: object) -> None:
    kind = rule.get("type")
    if kind in {"integer", "number"}:
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not _finite_number(value)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, f"invalid strategy parameter: {name} type")
        if kind == "integer" and type(value) is not int:
            raise PortError(ErrorCode.INVALID_INPUT, f"invalid strategy parameter: {name} type")
        minimum, maximum = rule.get("minimum"), rule.get("maximum")
        if isinstance(minimum, int | float) and not isinstance(minimum, bool) and value < minimum:
            raise PortError(ErrorCode.INVALID_INPUT, f"invalid strategy parameter: {name} minimum")
        if isinstance(maximum, int | float) and not isinstance(maximum, bool) and value > maximum:
            raise PortError(ErrorCode.INVALID_INPUT, f"invalid strategy parameter: {name} maximum")
    elif kind == "string":
        if not isinstance(value, str):
            raise PortError(ErrorCode.INVALID_INPUT, f"invalid strategy parameter: {name} type")
        min_length, max_length = rule.get("minLength"), rule.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise PortError(
                ErrorCode.INVALID_INPUT, f"invalid strategy parameter: {name} minimum length"
            )
        if isinstance(max_length, int) and len(value) > max_length:
            raise PortError(
                ErrorCode.INVALID_INPUT, f"invalid strategy parameter: {name} maximum length"
            )
    elif kind == "boolean":
        if type(value) is not bool:
            raise PortError(ErrorCode.INVALID_INPUT, f"invalid strategy parameter: {name} type")
    else:
        raise PortError(ErrorCode.INVALID_INPUT, f"invalid strategy parameter schema: {name}")
    enum = rule.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise PortError(ErrorCode.INVALID_INPUT, f"invalid strategy parameter: {name} value")


@dataclass(frozen=True, slots=True)
class Strategy:
    strategy_id: UUID
    workspace_id: UUID
    name: str
    description: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    current_version: int
    archived: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.strategy_id, UUID)
            or not isinstance(self.workspace_id, UUID)
            or not isinstance(self.created_by, UUID)
            or not isinstance(self.name, str)
            or not 1 <= len(self.name.strip()) <= 120
            or not isinstance(self.description, str)
            or len(self.description) > 2000
            or not isinstance(self.created_at, datetime)
            or self.created_at.utcoffset() is None
            or not isinstance(self.updated_at, datetime)
            or self.updated_at.utcoffset() is None
            or self.updated_at < self.created_at
            or type(self.current_version) is not int
            or self.current_version < 1
            or type(self.archived) is not bool
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy")


class StrategyPort(Protocol):
    """Stores a mutable Strategy root with append-only Strategy Versions."""

    def create(self, strategy: Strategy, version: StrategyVersion) -> None: ...

    def get(self, workspace_id: UUID, strategy_id: UUID) -> Strategy | None: ...

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[Strategy, ...]: ...

    def versions(self, workspace_id: UUID, strategy_id: UUID) -> tuple[StrategyVersion, ...]: ...

    def append_version(
        self,
        workspace_id: UUID,
        strategy_id: UUID,
        expected_version: int,
        strategy: Strategy,
        version: StrategyVersion,
    ) -> None: ...


class StrategyVersionState(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    BACKTESTED = "backtested"
    PAPER = "paper"
    APPROVED = "approved"
    LIVE = "live"
    PAUSED = "paused"
    FAILED = "failed"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class StrategyVersionLifecycle:
    workspace_id: UUID
    strategy_id: UUID
    strategy_version_id: UUID
    version: int
    state: StrategyVersionState
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.strategy_id, UUID)
            or not isinstance(self.strategy_version_id, UUID)
            or type(self.version) is not int
            or self.version < 1
            or not isinstance(self.state, StrategyVersionState)
            or not isinstance(self.updated_at, datetime)
            or self.updated_at.utcoffset() is None
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy version lifecycle")


@dataclass(frozen=True, slots=True)
class StrategyStateTransition:
    transition_id: UUID
    workspace_id: UUID
    strategy_id: UUID
    strategy_version_id: UUID
    version: int
    from_state: StrategyVersionState
    to_state: StrategyVersionState
    actor_id: UUID
    occurred_at: datetime
    reason: str = ""
    evidence_json: str = "{}"

    def __post_init__(self) -> None:
        try:
            if (
                any(
                    not isinstance(value, UUID)
                    for value in (
                        self.transition_id,
                        self.workspace_id,
                        self.strategy_id,
                        self.strategy_version_id,
                        self.actor_id,
                    )
                )
                or type(self.version) is not int
                or self.version < 1
                or not isinstance(self.from_state, StrategyVersionState)
                or not isinstance(self.to_state, StrategyVersionState)
                or self.from_state == self.to_state
                or not isinstance(self.occurred_at, datetime)
                or self.occurred_at.utcoffset() is None
                or not isinstance(self.reason, str)
                or len(self.reason) > 512
                or any(char in self.reason for char in "\r\n")
                or not isinstance(self.evidence_json, str)
                or not isinstance(json.loads(self.evidence_json), dict)
            ):
                raise ValueError
        except (TypeError, ValueError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy state transition") from None


@dataclass(frozen=True, slots=True)
class StrategyVersionApproval:
    approval_id: UUID
    workspace_id: UUID
    strategy_id: UUID
    strategy_version_id: UUID
    version: int
    approver_id: UUID
    approved_at: datetime
    backtest_run_id: UUID
    paper_observations: int
    reason: str = ""

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(value, UUID)
                for value in (
                    self.approval_id,
                    self.workspace_id,
                    self.strategy_id,
                    self.strategy_version_id,
                    self.approver_id,
                    self.backtest_run_id,
                )
            )
            or type(self.version) is not int
            or self.version < 1
            or not isinstance(self.approved_at, datetime)
            or self.approved_at.utcoffset() is None
            or type(self.paper_observations) is not int
            or self.paper_observations < 1
            or not isinstance(self.reason, str)
            or len(self.reason) > 512
            or any(char in self.reason for char in "\r\n")
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy version approval")


@dataclass(frozen=True, slots=True)
class StrategyQualification:
    backtest_run_id: UUID | None = None
    backtest_succeeded: bool = False
    paper_observations: int = 0
    required_paper_observations: int = 20

    def __post_init__(self) -> None:
        if (
            (self.backtest_run_id is not None and not isinstance(self.backtest_run_id, UUID))
            or type(self.backtest_succeeded) is not bool
            or type(self.paper_observations) is not int
            or self.paper_observations < 0
            or type(self.required_paper_observations) is not int
            or self.required_paper_observations < 1
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy qualification")


class StrategyLifecyclePort(Protocol):
    """Atomic status, transition history, approval and strategy archive operations."""

    def state(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> StrategyVersionLifecycle: ...

    def history(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> tuple[StrategyStateTransition, ...]: ...

    def approval(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> StrategyVersionApproval | None: ...

    def transition(
        self,
        expected_state: StrategyVersionState,
        lifecycle: StrategyVersionLifecycle,
        transition: StrategyStateTransition,
    ) -> StrategyVersionLifecycle: ...

    def approve(
        self,
        expected_state: StrategyVersionState,
        lifecycle: StrategyVersionLifecycle,
        transition: StrategyStateTransition,
        approval: StrategyVersionApproval,
    ) -> StrategyVersionLifecycle: ...

    def archive_strategy(
        self, workspace_id: UUID, strategy_id: UUID, actor_id: UUID, now: datetime
    ) -> Strategy: ...


class StrategyQualificationPort(Protocol):
    def for_version(
        self, workspace_id: UUID, strategy_id: UUID, version: int
    ) -> StrategyQualification: ...


class StrategyRuntimePort(Protocol):
    """Execution-context facts for a specific immutable version."""

    def is_live(self, workspace_id: UUID, strategy_id: UUID, strategy_version_id: UUID) -> bool: ...
