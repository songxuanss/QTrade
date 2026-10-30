from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from qtrade_application.realtime import RealtimeMarketDataService
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.realtime import (
    MarketStreamKey,
    MarketTick,
    ProviderPulse,
    RealtimePolicy,
)
from qtrade_mock.realtime import MockRealtimeHealthStore, ScriptedRealtimeProvider


@pytest.fixture
def setup():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    workspace = Workspace(uuid4(), "Realtime", "organization", None, False, False, now)
    access = WorkspaceAccess(workspace, uuid4(), Role.ADMIN)
    key = MarketStreamKey(workspace.workspace_id, "mock-feed", "MOCK:ABC")
    provider = ScriptedRealtimeProvider(
        "mock-feed",
        [
            ProviderPulse(
                True,
                now,
                (MarketTick(1, now - timedelta(milliseconds=50), Decimal("10")),),
            ),
            ProviderPulse(
                True,
                now + timedelta(seconds=1),
                (MarketTick(3, now + timedelta(milliseconds=100), Decimal("11")),),
            ),
            ProviderPulse(
                True,
                now + timedelta(seconds=2),
                (MarketTick(2, now + timedelta(seconds=1), Decimal("12")),),
            ),
            ProviderPulse(False, None),
            ProviderPulse(
                True,
                now + timedelta(milliseconds=7500),
                (MarketTick(4, now + timedelta(milliseconds=7500), Decimal("13")),),
            ),
        ],
    )
    store = MockRealtimeHealthStore(
        lambda actor, scope: access if actor == access.user_id else None
    )
    service = RealtimeMarketDataService(
        store,
        (provider,),
        RealtimePolicy(
            heartbeat_timeout_seconds=3, maximum_lag_ms=800, reconnect_backoff_seconds=5
        ),
    )
    return now, access, key, provider, store, service


def test_heartbeat_lag_out_of_order_disconnect_and_reconnect_are_exposed(setup):
    now, access, key, provider, store, service = setup
    healthy = service.poll_once(access, key, now)
    assert healthy.status == "healthy" and healthy.latest_lag_ms == 50
    delayed = service.poll_once(access, key, now + timedelta(seconds=1))
    assert delayed.status == "degraded" and delayed.latest_lag_ms == 900
    out_of_order = service.poll_once(access, key, now + timedelta(seconds=2))
    assert out_of_order.status == "degraded" and out_of_order.out_of_order_count == 1
    disconnected = service.poll_once(access, key, now + timedelta(seconds=3))
    assert (
        disconnected.status == "disconnected"
        and disconnected.next_reconnect_at == now + timedelta(seconds=8)
    )
    deferred = service.poll_once(access, key, now + timedelta(seconds=7))
    assert deferred == disconnected and provider.poll_count == 4
    recovered = service.poll_once(access, key, now + timedelta(seconds=8))
    assert recovered.status == "healthy" and recovered.reconnect_count == 1
    assert recovered.last_sequence == 4 and provider.reconnect_count == 2
    assert service.status(access, key) == recovered and store.list(
        access.workspace.workspace_id
    ) == (recovered,)


def test_stale_heartbeat_is_disconnected_and_writer_permission_is_required(setup):
    now, access, key, provider, _, service = setup
    service.poll_once(access, key, now)
    provider._pulses.insert(0, ProviderPulse(True, now - timedelta(seconds=10)))
    stale = service.poll_once(access, key, now + timedelta(seconds=1))
    assert stale.status == "disconnected" and stale.last_error_code == ErrorCode.TIMEOUT
    viewer = replace(access, role=Role.VIEWER)
    with pytest.raises(PortError) as denied:
        service.poll_once(viewer, key, now + timedelta(seconds=10))
    assert denied.value.code == ErrorCode.REJECTED


def test_workspace_mismatch_and_unconfigured_provider_fail_closed(setup):
    now, access, key, _, _, service = setup
    with pytest.raises(PortError) as mismatch:
        service.poll_once(access, MarketStreamKey(uuid4(), key.source, key.instrument_id), now)
    assert mismatch.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(PortError) as unsupported:
        service.poll_once(
            access, MarketStreamKey(key.workspace_id, "unknown", key.instrument_id), now
        )
    assert unsupported.value.code == ErrorCode.UNSUPPORTED
