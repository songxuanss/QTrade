"""Scriptable provider and concurrent connection-health storage double."""

from collections.abc import Callable
from threading import RLock
from uuid import UUID

from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.realtime import (
    MarketStreamHealth,
    MarketStreamKey,
    ProviderPulse,
)


class ScriptedRealtimeProvider:
    def __init__(self, source: str, pulses: list[ProviderPulse | Exception]):
        self._source = source
        self._pulses = list(pulses)
        self.reconnect_count = 0
        self.poll_count = 0

    @property
    def source(self) -> str:
        return self._source

    def reconnect(self, key: MarketStreamKey) -> None:
        if key.source != self._source:
            raise PortError(ErrorCode.UNSUPPORTED, "mock source mismatch")
        self.reconnect_count += 1

    def poll(self, key: MarketStreamKey, timeout_seconds: float) -> ProviderPulse:
        if key.source != self._source or timeout_seconds <= 0:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid mock poll")
        self.poll_count += 1
        if not self._pulses:
            raise PortError(ErrorCode.UNAVAILABLE, "no scripted provider pulse")
        pulse = self._pulses.pop(0)
        if isinstance(pulse, Exception):
            raise pulse
        return pulse


class MockRealtimeHealthStore:
    def __init__(self, access_for_actor: Callable[[UUID, UUID], WorkspaceAccess | None]):
        self._access_for_actor = access_for_actor
        self._health: dict[tuple[UUID, str, str], MarketStreamHealth] = {}
        self._lock = RLock()

    @staticmethod
    def _key(key: MarketStreamKey) -> tuple[UUID, str, str]:
        return key.workspace_id, key.source, key.instrument_id

    def get(self, key: MarketStreamKey) -> MarketStreamHealth | None:
        with self._lock:
            return self._health.get(self._key(key))

    def put(self, actor_id: UUID, health: MarketStreamHealth) -> None:
        access = self._access_for_actor(actor_id, health.key.workspace_id)
        if access is None or access.workspace.workspace_id != health.key.workspace_id:
            raise PortError(ErrorCode.NOT_FOUND, "workspace resource unavailable")
        access.require(Permission.DATA_MANAGE, object_workspace_id=health.key.workspace_id)
        with self._lock:
            self._health[self._key(health.key)] = health

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[MarketStreamHealth, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid market health page size")
        with self._lock:
            rows = sorted(
                (item for item in self._health.values() if item.key.workspace_id == workspace_id),
                key=lambda item: (item.key.source, item.key.instrument_id),
            )
        return tuple(rows[:limit])
