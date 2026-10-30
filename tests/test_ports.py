"""Execute complete port interactions through structural Protocol types."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from qtrade_contracts import ArtifactManifest, BacktestRun, Bar, OrderCommand
from qtrade_domain.ports import (
    BarQuery,
    BrokerMonitoringPort,
    BrokerPort,
    ErrorCode,
    MarketDataPort,
    Notification,
    NotificationPort,
    ObjectStorePort,
    PortError,
    ResearchPort,
    ResearchRequest,
    TradingEnginePort,
)
from qtrade_mock.ports import (
    MockBroker,
    MockMarketData,
    MockNotification,
    MockObjectStore,
    MockResearch,
    MockTradingEngine,
)

FIXTURES = Path(__file__).parent / "fixtures/contracts"


def load(model):
    return model.model_validate_json((FIXTURES / f"{model.__name__}.json").read_text())


def assert_error(code, action):
    with pytest.raises(PortError) as caught:
        action()
    assert caught.value.code == code


@pytest.mark.parametrize(
    "protocol,implementation",
    [
        (TradingEnginePort, MockTradingEngine),
        (MarketDataPort, MockMarketData),
        (BrokerPort, MockBroker),
        (ResearchPort, MockResearch),
        (NotificationPort, MockNotification),
        (ObjectStorePort, MockObjectStore),
    ],
)
def test_structural_conformance(protocol, implementation):
    assert isinstance(implementation(), protocol)


def test_object_store_roundtrip_idempotency_integrity_and_scope():
    store: ObjectStorePort = MockObjectStore()
    workspace = uuid4()
    content = b"immutable artifact"
    digest = sha256(content).hexdigest()
    ref = store.put(
        workspace,
        "runs/report.json",
        content,
        content_type="application/json",
        expected_sha256=digest,
    )
    assert ref.size == len(content)
    assert (
        store.put(
            workspace, ref.key, content, content_type=ref.content_type, expected_sha256=digest
        )
        == ref
    )
    assert store.stat(workspace, ref.key) == ref
    assert store.get(workspace, ref.key, expected_sha256=digest) == content
    assert_error(ErrorCode.NOT_FOUND, lambda: store.stat(uuid4(), ref.key))
    assert_error(
        ErrorCode.NOT_FOUND, lambda: store.get(workspace, "missing", expected_sha256=digest)
    )
    assert_error(
        ErrorCode.CHECKSUM_MISMATCH, lambda: store.get(workspace, ref.key, expected_sha256="a" * 64)
    )
    assert_error(
        ErrorCode.CHECKSUM_MISMATCH,
        lambda: store.put(
            workspace, "new", content, content_type="text/plain", expected_sha256="a" * 64
        ),
    )
    assert_error(
        ErrorCode.CONFLICT,
        lambda: store.put(
            workspace,
            ref.key,
            b"changed",
            content_type=ref.content_type,
            expected_sha256=sha256(b"changed").hexdigest(),
        ),
    )
    assert_error(
        ErrorCode.INVALID_INPUT, lambda: store.get(workspace, "../escape", expected_sha256=digest)
    )


def test_market_data_query_pagination_ranges_and_immutable_write():
    port: MarketDataPort = MockMarketData()
    first = load(Bar)
    second = first.model_copy(update={"timestamp": first.timestamp + timedelta(minutes=1)})
    port.write_bars(first.workspace_id, first.dataset_id, (second, first))
    port.write_bars(first.workspace_id, first.dataset_id, (first, second))
    query = BarQuery(
        first.workspace_id,
        first.dataset_id,
        first.instrument_id,
        first.granularity,
        first.timestamp,
        second.timestamp + timedelta(minutes=1),
        limit=1,
    )
    page = port.query_bars(query)
    assert page.bars == (first,)
    assert port.query_bars(replace(query, cursor=page.next_cursor)).bars == (second,)
    assert port.query_bars(replace(query, cursor=page.next_cursor)).next_cursor is None
    assert port.query_bars(replace(query, end=second.timestamp)).bars == (first,)
    assert port.query_bars(replace(query, instrument_id="missing")).bars == ()
    assert_error(ErrorCode.NOT_FOUND, lambda: port.query_bars(replace(query, workspace_id=uuid4())))
    assert_error(ErrorCode.INVALID_INPUT, lambda: port.query_bars(replace(query, cursor="-1")))
    assert_error(
        ErrorCode.INVALID_INPUT, lambda: port.query_bars(replace(query, cursor="nonsense"))
    )
    assert_error(
        ErrorCode.CONFLICT, lambda: port.write_bars(first.workspace_id, first.dataset_id, (first,))
    )
    assert_error(
        ErrorCode.INVALID_INPUT,
        lambda: port.write_bars(first.workspace_id, first.dataset_id, (first, first)),
    )
    assert_error(ErrorCode.INVALID_INPUT, lambda: replace(query, start=query.end))
    assert_error(ErrorCode.INVALID_INPUT, lambda: replace(query, limit=0))


def test_engine_job_submit_poll_complete_and_cancel():
    mock = MockTradingEngine()
    port: TradingEnginePort = mock
    run, manifest = load(BacktestRun), load(ArtifactManifest)
    job = port.submit(run, manifest)
    assert job.status == "queued"
    assert port.submit(run, manifest) == job
    assert port.status(run.workspace_id, run.run_id) == job
    assert_error(
        ErrorCode.INVALID_INPUT,
        lambda: port.submit(run, manifest.model_copy(update={"workspace_id": uuid4()})),
    )
    assert_error(
        ErrorCode.CONFLICT,
        lambda: port.submit(
            run, manifest.model_copy(update={"random_seed": manifest.random_seed + 1})
        ),
    )
    store = MockObjectStore()
    content = b"result"
    ref = store.put(
        run.workspace_id,
        "result",
        content,
        content_type="application/json",
        expected_sha256=sha256(content).hexdigest(),
    )
    assert mock.complete(run.workspace_id, run.run_id, ref).progress == 100
    assert port.status(run.workspace_id, run.run_id).result == ref
    assert_error(ErrorCode.CONFLICT, lambda: port.cancel(run.workspace_id, run.run_id))
    other = run.model_copy(update={"run_id": uuid4(), "manifest_id": uuid4()})
    other_manifest = manifest.model_copy(
        update={"run_id": other.run_id, "manifest_id": other.manifest_id}
    )
    port.submit(other, other_manifest)
    assert port.cancel(other.workspace_id, other.run_id).status == "cancelled"
    assert_error(ErrorCode.CONFLICT, lambda: mock.complete(other.workspace_id, other.run_id, ref))
    assert_error(ErrorCode.NOT_FOUND, lambda: port.status(uuid4(), run.run_id))


def test_research_port_submit_poll_and_cancel():
    port: ResearchPort = MockResearch()
    manifest = load(ArtifactManifest).model_copy(update={"kind": "research"})
    request = ResearchRequest(manifest, "factor_evaluation", ())
    assert port.submit(request) == port.submit(request)
    assert port.status(manifest.workspace_id, manifest.run_id).status == "queued"
    assert_error(ErrorCode.CONFLICT, lambda: port.submit(replace(request, operation="different")))
    assert port.cancel(manifest.workspace_id, manifest.run_id).status == "cancelled"
    assert_error(
        ErrorCode.INVALID_INPUT,
        lambda: port.submit(replace(request, manifest=load(ArtifactManifest))),
    )


def test_broker_port_idempotency_cancel_and_account_snapshot():
    port: BrokerPort = MockBroker()
    command = load(OrderCommand)
    order = port.submit(command)
    assert port.submit(command) == order
    assert order.quantity == command.intent.quantity
    assert order.filled_quantity == 0
    assert_error(
        ErrorCode.CONFLICT, lambda: port.submit(command.model_copy(update={"command_id": uuid4()}))
    )
    assert port.snapshot(command.workspace_id, command.intent.account_id).orders == (order,)
    assert port.snapshot(uuid4(), command.intent.account_id).orders == ()
    assert_error(
        ErrorCode.NOT_FOUND,
        lambda: port.cancel(uuid4(), command.intent.account_id, order.broker_order_id),
    )
    cancelled = port.cancel(command.workspace_id, command.intent.account_id, order.broker_order_id)
    assert cancelled.status == "cancelled"
    assert port.submit(command) == cancelled
    assert port.snapshot(command.workspace_id, command.intent.account_id).orders == (cancelled,)


def test_broker_monitoring_partial_fills_rejection_timeout_and_disconnect():
    command = load(OrderCommand)
    broker = MockBroker()
    monitor: BrokerMonitoringPort = broker
    scope = (command.workspace_id, command.intent.account_id)
    assert monitor.connection_status(*scope).connected
    broker.submit(command)
    partial = broker.fill(command.idempotency_key, command.intent.quantity / 2, Decimal("12.5"))
    assert partial.status == "partially_filled"
    filled = broker.fill(command.idempotency_key, command.intent.quantity, Decimal("12.5"))
    assert filled.status == "filled"
    events = monitor.events(*scope, limit=10)
    assert [event.event_type for event in events] == ["submitted", "partially_filled", "filled"]
    assert events[-1].filled_quantity == command.intent.quantity
    assert monitor.events(*scope, after_sequence=1, limit=10) == events[1:]

    rejected = MockBroker()
    rejected.submit(command)
    assert rejected.reject(command.idempotency_key, "broker_risk_rejection").status == "rejected"
    assert rejected.events(*scope)[-1].error_code == "broker_risk_rejection"

    timeout = MockBroker()
    timeout.fail_next_submission(*scope, ErrorCode.TIMEOUT)
    assert_error(ErrorCode.TIMEOUT, lambda: timeout.submit(command))
    disconnected = MockBroker()
    disconnected.set_connected(*scope, False, error_code="transport_down")
    assert not disconnected.connection_status(*scope).connected
    assert_error(ErrorCode.UNAVAILABLE, lambda: disconnected.submit(command))


def test_notification_deduplication_and_scoped_identity():
    port: NotificationPort = MockNotification()
    notification = Notification(uuid4(), uuid4(), "in_app", "member-1", "Risk", "Limit reached")
    assert port.send(notification) == port.send(notification)
    assert port.send(notification).status == "delivered"
    assert_error(ErrorCode.CONFLICT, lambda: port.send(replace(notification, body="changed")))
    assert port.send(replace(notification, workspace_id=uuid4())).status == "delivered"


@pytest.mark.parametrize("code", ErrorCode)
def test_standard_errors_have_explicit_retry_policy(code):
    error = PortError(
        code, "safe summary", retry_after=2 if code == ErrorCode.RATE_LIMITED else None
    )
    assert error.retryable == (
        code in {ErrorCode.RATE_LIMITED, ErrorCode.UNAVAILABLE, ErrorCode.TIMEOUT}
    )
    assert str(error) == "safe summary"
