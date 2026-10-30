from dataclasses import FrozenInstanceError, replace

import pytest

from qtrade_application.capabilities import CapabilityRegistry
from qtrade_domain.capabilities import (
    AdapterHealth,
    AdapterKind,
    CapabilityDescriptor,
    CapabilityRequest,
)
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.ports import MockBroker


def broker():
    return CapabilityDescriptor(
        "paper",
        "Paper broker",
        AdapterKind.BROKER,
        markets=("simulation",),
        order_types=("market", "limit"),
        trading_sessions=("continuous",),
        health=AdapterHealth.HEALTHY,
    )


@pytest.mark.parametrize(
    "selection",
    [
        CapabilityRequest(AdapterKind.ENGINE),
        CapabilityRequest(AdapterKind.BROKER, market="equities"),
        CapabilityRequest(AdapterKind.BROKER, granularity="1m"),
        CapabilityRequest(AdapterKind.BROKER, order_type="stop_limit"),
        CapabilityRequest(AdapterKind.BROKER, trading_session="extended"),
        CapabilityRequest(AdapterKind.BROKER, live=True),
    ],
)
def test_selection_rejects_unsupported_capability_before_returning_adapter(selection):
    registry = CapabilityRegistry[object]()
    registry.register(broker(), MockBroker())
    with pytest.raises(PortError) as failure:
        registry.require("paper", selection)
    assert failure.value.code == ErrorCode.UNSUPPORTED


def test_registry_is_ordered_unique_and_returns_the_bound_adapter():
    registry = CapabilityRegistry[object]()
    adapter = MockBroker()
    registry.register(broker(), adapter)
    registry.register(replace(broker(), adapter_id="another"), MockBroker())
    assert [d.adapter_id for d in registry.descriptors()] == ["another", "paper"]
    assert (
        registry.require(
            "paper",
            CapabilityRequest(
                AdapterKind.BROKER,
                market="simulation",
                order_type="limit",
                trading_session="continuous",
            ),
        ).adapter
        is adapter
    )
    with pytest.raises(PortError) as duplicate:
        registry.register(broker(), MockBroker())
    assert duplicate.value.code == ErrorCode.CONFLICT
    with pytest.raises(PortError) as missing:
        registry.require("absent", CapabilityRequest(AdapterKind.BROKER))
    assert missing.value.code == ErrorCode.NOT_FOUND


@pytest.mark.parametrize(
    "health",
    [
        AdapterHealth.UNKNOWN,
        AdapterHealth.DEGRADED,
        AdapterHealth.UNAVAILABLE,
    ],
)
def test_health_changes_fail_closed_and_recovery_preserves_capabilities(health):
    registry = CapabilityRegistry[object]()
    adapter = MockBroker()
    registry.register(broker(), adapter)
    old = registry.descriptors()[0]
    registry.health("paper", health)
    assert old.health == AdapterHealth.HEALTHY
    with pytest.raises(PortError) as failure:
        registry.require("paper", CapabilityRequest(AdapterKind.BROKER))
    assert failure.value.code == ErrorCode.UNAVAILABLE
    registry.health("paper", AdapterHealth.HEALTHY)
    assert registry.require("paper", CapabilityRequest(AdapterKind.BROKER)).adapter is adapter
    assert registry.descriptors()[0].order_types == ("market", "limit")


@pytest.mark.parametrize(
    "changes",
    [
        {"markets": ("simulation", "simulation")},
        {"markets": ["simulation"]},
        {"granularities": ("2m",)},
        {"order_types": ("bracket",)},
        {"adapter_id": "contains secrets?"},
        {"live": "true"},
        {"version": 2},
        {"health": "healthy"},
        {"trading_sessions": ("",)},
    ],
)
def test_descriptor_rejects_invalid_or_mutable_declarations(changes):
    with pytest.raises(ValueError):
        replace(broker(), **changes)


def test_descriptor_cannot_be_mutated():
    with pytest.raises(FrozenInstanceError):
        broker().live = True


def test_declared_kind_must_match_an_actual_port():
    registry = CapabilityRegistry[object]()
    with pytest.raises(PortError) as failure:
        registry.register(broker(), object())
    assert failure.value.code == ErrorCode.INVALID_INPUT
    assert registry.descriptors() == ()


def test_all_mock_declarations_bind_actual_adapters_and_never_claim_live():
    from qtrade_mock.capabilities import DESCRIPTORS
    from qtrade_mock.ports import (
        MockMarketData,
        MockNotification,
        MockObjectStore,
        MockResearch,
        MockTradingEngine,
    )

    registry = CapabilityRegistry[object]()
    adapters = {
        AdapterKind.ENGINE: MockTradingEngine(),
        AdapterKind.MARKET_DATA: MockMarketData(),
        AdapterKind.BROKER: MockBroker(),
        AdapterKind.RESEARCH: MockResearch(),
        AdapterKind.OBJECT_STORE: MockObjectStore(),
        AdapterKind.NOTIFICATION: MockNotification(),
    }
    for descriptor in DESCRIPTORS:
        registry.register(descriptor, adapters[descriptor.kind])
        assert not descriptor.live
        if descriptor.health == AdapterHealth.HEALTHY:
            assert (
                registry.require(descriptor.adapter_id, CapabilityRequest(descriptor.kind)).adapter
                is adapters[descriptor.kind]
            )
    assert len(registry.descriptors()) == 6


def test_selected_market_adapter_executes_the_declared_interval():
    from datetime import timedelta
    from pathlib import Path

    from qtrade_contracts import Bar
    from qtrade_domain.ports import BarQuery, MarketDataPort
    from qtrade_mock.capabilities import DESCRIPTORS
    from qtrade_mock.ports import MockMarketData

    bar = Bar.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Bar.json").read_text()
    )
    registry = CapabilityRegistry[MarketDataPort]()
    descriptor = next(d for d in DESCRIPTORS if d.kind == AdapterKind.MARKET_DATA)
    registry.register(descriptor, MockMarketData())
    selected = registry.require(
        descriptor.adapter_id,
        CapabilityRequest(
            AdapterKind.MARKET_DATA, market="simulation", granularity=bar.granularity
        ),
    )
    selected.adapter.write_bars(bar.workspace_id, bar.dataset_id, (bar,))
    result = selected.adapter.query_bars(
        BarQuery(
            bar.workspace_id,
            bar.dataset_id,
            bar.instrument_id,
            bar.granularity,
            bar.timestamp,
            bar.timestamp + timedelta(days=1),
        )
    )
    assert result.bars == (bar,)
