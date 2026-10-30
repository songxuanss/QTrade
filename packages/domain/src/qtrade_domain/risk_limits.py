"""Versioned, hierarchical risk limits and projected usage calculations."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from qtrade_domain.ports import ErrorCode, PortError

LimitDimension = Literal["account", "strategy", "instrument", "workspace"]
LimitMetric = Literal[
    "position_quantity",
    "notional_exposure",
    "order_frequency",
    "daily_loss",
    "drawdown",
    "leverage",
]


@dataclass(frozen=True)
class RiskLimit:
    limit_id: str
    dimension: LimitDimension
    scope_id: str
    metric: LimitMetric
    maximum: Decimal
    version: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.limit_id, str)
            or not 1 <= len(self.limit_id) <= 128
            or self.dimension not in {"account", "strategy", "instrument", "workspace"}
            or not isinstance(self.scope_id, str)
            or not 1 <= len(self.scope_id) <= 128
            or self.metric
            not in {
                "position_quantity",
                "notional_exposure",
                "order_frequency",
                "daily_loss",
                "drawdown",
                "leverage",
            }
            or not isinstance(self.maximum, Decimal)
            or not self.maximum.is_finite()
            or self.maximum <= 0
            or not isinstance(self.version, str)
            or not 1 <= len(self.version) <= 128
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid risk limit")


@dataclass(frozen=True)
class LimitProjection:
    dimension: LimitDimension
    scope_id: str
    metric: LimitMetric
    current_value: Decimal
    projected_value: Decimal

    def __post_init__(self) -> None:
        if (
            self.dimension not in {"account", "strategy", "instrument", "workspace"}
            or not isinstance(self.scope_id, str)
            or not 1 <= len(self.scope_id) <= 128
            or self.metric
            not in {
                "position_quantity",
                "notional_exposure",
                "order_frequency",
                "daily_loss",
                "drawdown",
                "leverage",
            }
            or not isinstance(self.current_value, Decimal)
            or not self.current_value.is_finite()
            or self.current_value < 0
            or not isinstance(self.projected_value, Decimal)
            or not self.projected_value.is_finite()
            or self.projected_value < 0
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid risk limit projection")


@dataclass(frozen=True)
class LimitUsage:
    limit: RiskLimit
    current_value: Decimal
    projected_value: Decimal
    utilization: Decimal
    breached: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.limit, RiskLimit)
            or not isinstance(self.current_value, Decimal)
            or not self.current_value.is_finite()
            or self.current_value < 0
            or not isinstance(self.projected_value, Decimal)
            or not self.projected_value.is_finite()
            or self.projected_value < 0
            or not isinstance(self.utilization, Decimal)
            or not self.utilization.is_finite()
            or self.utilization != self.projected_value / self.limit.maximum
            or type(self.breached) is not bool
            or self.breached != (self.projected_value > self.limit.maximum)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "inconsistent risk limit usage")


class RiskLimitStore(Protocol):
    def list(self, workspace_id: UUID) -> tuple[RiskLimit, ...]: ...


def calculate_limit_usage(
    limits: tuple[RiskLimit, ...], projections: tuple[LimitProjection, ...]
) -> tuple[LimitUsage, ...]:
    """Calculate usage for every configured scope and reject ambiguous inputs."""
    keys = tuple((item.dimension, item.scope_id, item.metric) for item in limits)
    projection_keys = tuple((item.dimension, item.scope_id, item.metric) for item in projections)
    if (
        not limits
        or len({item.limit_id for item in limits}) != len(limits)
        or len(set(keys)) != len(keys)
        or len(set(projection_keys)) != len(projection_keys)
        or set(keys) != set(projection_keys)
    ):
        raise PortError(
            ErrorCode.INVALID_INPUT,
            "each risk limit requires one unique matching projection",
        )
    projected_by_key = {(item.dimension, item.scope_id, item.metric): item for item in projections}
    return tuple(
        LimitUsage(
            limit=limit,
            current_value=projected_by_key[
                (limit.dimension, limit.scope_id, limit.metric)
            ].current_value,
            projected_value=projected_by_key[
                (limit.dimension, limit.scope_id, limit.metric)
            ].projected_value,
            utilization=(
                projected_by_key[(limit.dimension, limit.scope_id, limit.metric)].projected_value
                / limit.maximum
            ),
            breached=(
                projected_by_key[(limit.dimension, limit.scope_id, limit.metric)].projected_value
                > limit.maximum
            ),
        )
        for limit in limits
    )
