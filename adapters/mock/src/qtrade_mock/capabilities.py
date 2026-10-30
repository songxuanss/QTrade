"""Declarations for deterministic doubles; no external or live capability claims."""

from qtrade_domain.capabilities import AdapterHealth, AdapterKind, CapabilityDescriptor

DESCRIPTORS = (
    CapabilityDescriptor(
        "mock-engine",
        "Mock job engine",
        AdapterKind.ENGINE,
        markets=("simulation",),
        granularities=("1m", "5m", "15m", "1h", "1d"),
        health=AdapterHealth.UNKNOWN,
    ),
    CapabilityDescriptor(
        "mock-market",
        "Mock market data",
        AdapterKind.MARKET_DATA,
        markets=("simulation",),
        granularities=("1m", "5m", "15m", "1h", "1d"),
        health=AdapterHealth.HEALTHY,
    ),
    CapabilityDescriptor(
        "mock-broker",
        "Mock order recorder",
        AdapterKind.BROKER,
        markets=("simulation",),
        order_types=("market", "limit", "stop", "stop_limit"),
        trading_sessions=("continuous",),
        health=AdapterHealth.HEALTHY,
    ),
    CapabilityDescriptor("mock-research", "Mock research jobs", AdapterKind.RESEARCH),
    CapabilityDescriptor(
        "mock-objects",
        "Mock object store",
        AdapterKind.OBJECT_STORE,
        health=AdapterHealth.HEALTHY,
    ),
    CapabilityDescriptor(
        "mock-notifications",
        "Mock notification recorder",
        AdapterKind.NOTIFICATION,
        health=AdapterHealth.HEALTHY,
    ),
)
