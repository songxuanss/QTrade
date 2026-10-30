"""Provider-neutral market stream observations and durable health state."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol, get_args
from uuid import UUID

from qtrade_domain.ports import ErrorCode, PortError

ConnectionStatus = Literal["healthy", "degraded", "disconnected", "reconnecting"]


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PortError(ErrorCode.INVALID_INPUT, "aware realtime timestamp required")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class MarketStreamKey:
    workspace_id: UUID
    source: str
    instrument_id: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.source, str)
            or not 1 <= len(self.source) <= 256
            or not isinstance(self.instrument_id, str)
            or not 1 <= len(self.instrument_id) <= 256
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid realtime stream identity")


@dataclass(frozen=True)
class MarketTick:
    sequence: int
    event_at: datetime
    price: Decimal
    volume: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_at", utc(self.event_at))
        if (
            type(self.sequence) is not int
            or self.sequence < 0
            or not isinstance(self.price, Decimal)
            or not self.price.is_finite()
            or self.price <= 0
            or not isinstance(self.volume, Decimal)
            or not self.volume.is_finite()
            or self.volume < 0
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid realtime market tick")


@dataclass(frozen=True)
class ProviderPulse:
    connected: bool
    heartbeat_at: datetime | None
    ticks: tuple[MarketTick, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.connected) is not bool
            or not isinstance(self.ticks, tuple)
            or any(not isinstance(item, MarketTick) for item in self.ticks)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid realtime provider pulse")
        if self.heartbeat_at is not None:
            object.__setattr__(self, "heartbeat_at", utc(self.heartbeat_at))
        if self.connected != (self.heartbeat_at is not None):
            raise PortError(ErrorCode.INVALID_INPUT, "realtime heartbeat does not match connection")


@dataclass(frozen=True)
class MarketStreamHealth:
    key: MarketStreamKey
    status: ConnectionStatus
    updated_at: datetime
    heartbeat_at: datetime | None = None
    last_event_at: datetime | None = None
    last_sequence: int | None = None
    latest_lag_ms: int | None = None
    max_lag_ms: int = 0
    out_of_order_count: int = 0
    reconnect_count: int = 0
    next_reconnect_at: datetime | None = None
    last_error_code: ErrorCode | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "updated_at", utc(self.updated_at))
        for name in ("heartbeat_at", "last_event_at", "next_reconnect_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, utc(value))
        if (
            not isinstance(self.key, MarketStreamKey)
            or self.status not in get_args(ConnectionStatus)
            or (self.status in {"disconnected", "reconnecting"} and self.next_reconnect_at is None)
            or (self.status in {"healthy", "degraded"} and self.next_reconnect_at is not None)
            or type(self.max_lag_ms) is not int
            or self.max_lag_ms < 0
            or type(self.out_of_order_count) is not int
            or self.out_of_order_count < 0
            or type(self.reconnect_count) is not int
            or self.reconnect_count < 0
            or (
                self.last_sequence is not None
                and (type(self.last_sequence) is not int or self.last_sequence < 0)
            )
            or (
                self.latest_lag_ms is not None
                and (type(self.latest_lag_ms) is not int or self.latest_lag_ms < 0)
            )
            or (
                self.last_error_code is not None and not isinstance(self.last_error_code, ErrorCode)
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid realtime health state")


@dataclass(frozen=True)
class RealtimePolicy:
    heartbeat_timeout_seconds: float = 15
    maximum_lag_ms: int = 2000
    reconnect_backoff_seconds: float = 5

    def __post_init__(self) -> None:
        if (
            type(self.heartbeat_timeout_seconds) not in (int, float)
            or not math.isfinite(self.heartbeat_timeout_seconds)
            or self.heartbeat_timeout_seconds <= 0
            or type(self.maximum_lag_ms) is not int
            or self.maximum_lag_ms < 1
            or type(self.reconnect_backoff_seconds) not in (int, float)
            or not math.isfinite(self.reconnect_backoff_seconds)
            or self.reconnect_backoff_seconds <= 0
        ):
            raise ValueError("invalid realtime policy")


class RealtimeProviderPort(Protocol):
    @property
    def source(self) -> str: ...

    def reconnect(self, key: MarketStreamKey) -> None: ...

    def poll(self, key: MarketStreamKey, timeout_seconds: float) -> ProviderPulse: ...


class RealtimeHealthStorePort(Protocol):
    def get(self, key: MarketStreamKey) -> MarketStreamHealth | None: ...
    def put(self, actor_id: UUID, health: MarketStreamHealth) -> None: ...
    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[MarketStreamHealth, ...]: ...


def disconnected(
    key: MarketStreamKey,
    previous: MarketStreamHealth | None,
    now: datetime,
    policy: RealtimePolicy,
    error: ErrorCode | None = None,
) -> MarketStreamHealth:
    now = utc(now)
    reconnecting = previous is not None and previous.status in {"disconnected", "reconnecting"}
    return MarketStreamHealth(
        key,
        "reconnecting" if reconnecting else "disconnected",
        now,
        previous.heartbeat_at if previous else None,
        previous.last_event_at if previous else None,
        previous.last_sequence if previous else None,
        previous.latest_lag_ms if previous else None,
        previous.max_lag_ms if previous else 0,
        previous.out_of_order_count if previous else 0,
        (previous.reconnect_count if previous else 0) + int(reconnecting),
        now + timedelta(seconds=policy.reconnect_backoff_seconds),
        error,
    )
