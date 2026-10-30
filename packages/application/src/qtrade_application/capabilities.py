"""One selection interface binds capability declarations to concrete adapters."""

from dataclasses import dataclass, replace
from threading import RLock

from qtrade_domain.capabilities import (
    AdapterHealth,
    AdapterKind,
    CapabilityDescriptor,
    CapabilityRequest,
)
from qtrade_domain.ports import (
    BrokerPort,
    ErrorCode,
    MarketDataPort,
    NotificationPort,
    ObjectStorePort,
    PortError,
    ResearchPort,
    TradingEnginePort,
)

PORTS = {
    AdapterKind.ENGINE: TradingEnginePort,
    AdapterKind.MARKET_DATA: MarketDataPort,
    AdapterKind.BROKER: BrokerPort,
    AdapterKind.RESEARCH: ResearchPort,
    AdapterKind.OBJECT_STORE: ObjectStorePort,
    AdapterKind.NOTIFICATION: NotificationPort,
}


@dataclass(frozen=True)
class RegisteredAdapter[Adapter]:
    descriptor: CapabilityDescriptor
    adapter: Adapter


class CapabilityRegistry[Adapter]:
    def __init__(self) -> None:
        self._entries: dict[str, RegisteredAdapter[Adapter]] = {}
        self._lock = RLock()

    def register(self, descriptor: CapabilityDescriptor, adapter: Adapter) -> None:
        if not isinstance(adapter, PORTS[descriptor.kind]):
            raise PortError(ErrorCode.INVALID_INPUT, "adapter does not implement declared port")
        with self._lock:
            if descriptor.adapter_id in self._entries:
                raise PortError(ErrorCode.CONFLICT, "adapter already registered")
            self._entries[descriptor.adapter_id] = RegisteredAdapter(descriptor, adapter)

    def descriptors(self) -> tuple[CapabilityDescriptor, ...]:
        with self._lock:
            return tuple(self._entries[key].descriptor for key in sorted(self._entries))

    def _entry(self, adapter_id: str) -> RegisteredAdapter[Adapter]:
        try:
            return self._entries[adapter_id]
        except KeyError:
            raise PortError(ErrorCode.NOT_FOUND, "adapter not registered") from None

    def health(self, adapter_id: str, health: AdapterHealth) -> None:
        with self._lock:
            entry = self._entry(adapter_id)
            self._entries[adapter_id] = replace(
                entry, descriptor=replace(entry.descriptor, health=health)
            )

    def require(self, adapter_id: str, request: CapabilityRequest) -> RegisteredAdapter[Adapter]:
        with self._lock:
            entry = self._entry(adapter_id)
            entry.descriptor.require(request)
            return entry
