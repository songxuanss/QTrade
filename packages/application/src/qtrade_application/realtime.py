"""Realtime provider supervision and authorized connection-health queries."""

from datetime import datetime
from uuid import UUID

from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.realtime import (
    MarketStreamHealth,
    MarketStreamKey,
    ProviderPulse,
    RealtimeHealthStorePort,
    RealtimePolicy,
    RealtimeProviderPort,
    disconnected,
    utc,
)


class RealtimeMarketDataService:
    def __init__(
        self,
        store: RealtimeHealthStorePort,
        providers: tuple[RealtimeProviderPort, ...],
        policy: RealtimePolicy | None = None,
    ):
        self._store = store
        self._providers = {provider.source: provider for provider in providers}
        if len(self._providers) != len(providers):
            raise ValueError("duplicate realtime provider source")
        self._policy = policy or RealtimePolicy()

    def poll_once(
        self, access: WorkspaceAccess, key: MarketStreamKey, now: datetime
    ) -> MarketStreamHealth:
        access.require(Permission.DATA_MANAGE, object_workspace_id=key.workspace_id)
        if key.workspace_id != access.workspace.workspace_id:
            raise PortError(ErrorCode.NOT_FOUND, "market stream unavailable")
        provider = self._providers.get(key.source)
        if provider is None or provider.source != key.source:
            raise PortError(ErrorCode.UNSUPPORTED, "realtime source is not configured")
        now = utc(now)
        previous = self._store.get(key)
        if (
            previous is not None
            and previous.status in {"disconnected", "reconnecting"}
            and previous.next_reconnect_at is not None
            and previous.next_reconnect_at > now
        ):
            return previous
        reconnect_attempt = previous is None or previous.status in {"disconnected", "reconnecting"}
        if reconnect_attempt:
            try:
                provider.reconnect(key)
            except PortError as error:
                return self._save(
                    access.user_id,
                    disconnected(key, previous, now, self._policy, error.code),
                )
            except Exception:
                return self._save(
                    access.user_id,
                    disconnected(key, previous, now, self._policy, ErrorCode.UNAVAILABLE),
                )
        try:
            pulse = provider.poll(key, timeout_seconds=self._policy.heartbeat_timeout_seconds)
            if not isinstance(pulse, ProviderPulse):
                raise PortError(ErrorCode.UNAVAILABLE, "invalid realtime provider pulse")
        except PortError as error:
            return self._save(
                access.user_id, disconnected(key, previous, now, self._policy, error.code)
            )
        except Exception:
            return self._save(
                access.user_id,
                disconnected(key, previous, now, self._policy, ErrorCode.UNAVAILABLE),
            )
        if not pulse.connected or pulse.heartbeat_at is None:
            return self._save(
                access.user_id,
                disconnected(key, previous, now, self._policy, ErrorCode.UNAVAILABLE),
            )
        heartbeat = pulse.heartbeat_at
        if (
            heartbeat > now
            or (now - heartbeat).total_seconds() > self._policy.heartbeat_timeout_seconds
        ):
            stale = disconnected(key, previous, now, self._policy, ErrorCode.TIMEOUT)
            return self._save(access.user_id, stale)

        last_event = previous.last_event_at if previous else None
        last_sequence = previous.last_sequence if previous else None
        latest_lag = previous.latest_lag_ms if previous else None
        max_lag = previous.max_lag_ms if previous else 0
        out_of_order = previous.out_of_order_count if previous else 0
        lagging = False
        for tick in pulse.ticks:
            if (
                tick.event_at > now
                or (last_event is not None and tick.event_at <= last_event)
                or (last_sequence is not None and tick.sequence <= last_sequence)
            ):
                out_of_order += 1
                continue
            last_event, last_sequence = tick.event_at, tick.sequence
            lag_ms = max(0, int((now - tick.event_at).total_seconds() * 1000))
            latest_lag = lag_ms
            max_lag = max(max_lag, lag_ms)
            lagging = lagging or lag_ms > self._policy.maximum_lag_ms
        health = MarketStreamHealth(
            key,
            "degraded"
            if lagging or out_of_order > (previous.out_of_order_count if previous else 0)
            else "healthy",
            now,
            heartbeat,
            last_event,
            last_sequence,
            latest_lag,
            max_lag,
            out_of_order,
            (previous.reconnect_count if previous else 0)
            + int(previous is not None and reconnect_attempt),
        )
        return self._save(access.user_id, health)

    def status(self, access: WorkspaceAccess, key: MarketStreamKey) -> MarketStreamHealth:
        access.require(Permission.READ, object_workspace_id=key.workspace_id)
        if key.workspace_id != access.workspace.workspace_id:
            raise PortError(ErrorCode.NOT_FOUND, "market stream unavailable")
        result = self._store.get(key)
        if result is None:
            raise PortError(ErrorCode.NOT_FOUND, "market stream unavailable")
        return result

    def list(self, access: WorkspaceAccess, limit: int = 100) -> tuple[MarketStreamHealth, ...]:
        access.require(Permission.READ)
        return self._store.list(access.workspace.workspace_id, limit)

    def _save(self, actor_id: UUID, health: MarketStreamHealth) -> MarketStreamHealth:
        self._store.put(actor_id, health)
        return health
