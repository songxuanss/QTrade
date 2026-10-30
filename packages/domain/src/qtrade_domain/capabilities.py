"""Provider-neutral declarations and fail-closed capability selection."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import get_args

from qtrade_contracts.models import Granularity, OrderType
from qtrade_domain.ports import ErrorCode, PortError


class AdapterKind(StrEnum):
    ENGINE = "engine"
    MARKET_DATA = "market_data"
    BROKER = "broker"
    RESEARCH = "research"
    OBJECT_STORE = "object_store"
    NOTIFICATION = "notification"


class AdapterHealth(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


def identifiers(values: tuple[str, ...]) -> bool:
    return (
        isinstance(values, tuple)
        and len(values) == len(set(values))
        and all(
            isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value)
            for value in values
        )
    )


@dataclass(frozen=True)
class CapabilityRequest:
    kind: AdapterKind
    market: str | None = None
    granularity: Granularity | None = None
    order_type: OrderType | None = None
    trading_session: str | None = None
    live: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, AdapterKind)
            or type(self.live) is not bool
            or (self.market is not None and not identifiers((self.market,)))
            or (self.trading_session is not None and not identifiers((self.trading_session,)))
            or (self.granularity is not None and self.granularity not in get_args(Granularity))
            or (self.order_type is not None and self.order_type not in get_args(OrderType))
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid capability request")


@dataclass(frozen=True)
class CapabilityDescriptor:
    adapter_id: str
    name: str
    kind: AdapterKind
    markets: tuple[str, ...] = ()
    granularities: tuple[Granularity, ...] = ()
    order_types: tuple[OrderType, ...] = ()
    trading_sessions: tuple[str, ...] = ()
    live: bool = False
    health: AdapterHealth = AdapterHealth.UNKNOWN
    version: int = 1

    def __post_init__(self) -> None:
        if (
            not identifiers((self.adapter_id,))
            or not isinstance(self.name, str)
            or not 1 <= len(self.name) <= 128
            or not isinstance(self.kind, AdapterKind)
            or not isinstance(self.health, AdapterHealth)
            or type(self.live) is not bool
            or type(self.version) is not int
            or self.version != 1
            or not identifiers(self.markets)
            or not identifiers(self.trading_sessions)
            or not isinstance(self.granularities, tuple)
            or len(set(self.granularities)) != len(self.granularities)
            or any(value not in get_args(Granularity) for value in self.granularities)
            or not isinstance(self.order_types, tuple)
            or len(set(self.order_types)) != len(self.order_types)
            or any(value not in get_args(OrderType) for value in self.order_types)
        ):
            raise ValueError("invalid capability descriptor")

    def require(self, request: CapabilityRequest) -> None:
        if (
            request.kind != self.kind
            or (request.market is not None and request.market not in self.markets)
            or (request.granularity is not None and request.granularity not in self.granularities)
            or (request.order_type is not None and request.order_type not in self.order_types)
            or (
                request.trading_session is not None
                and request.trading_session not in self.trading_sessions
            )
            or (request.live and not self.live)
        ):
            raise PortError(ErrorCode.UNSUPPORTED, "adapter does not support requested capability")
        if self.health != AdapterHealth.HEALTHY:
            raise PortError(ErrorCode.UNAVAILABLE, "adapter is not healthy")
