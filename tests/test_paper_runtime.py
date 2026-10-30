from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from qtrade_application.paper_runtime import PaperRuntime, PaperRuntimeService
from qtrade_application.risk import RiskService
from qtrade_contracts import OrderIntent, RiskDecision, RiskReason
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.risk_records import RiskDecisionRecord
from qtrade_domain.strategies import StrategyVersionState
from qtrade_mock.paper_broker import PaperBroker
from qtrade_mock.ports import MockBroker


class _RiskStore:
    def __init__(self):
        self.records: list[RiskDecisionRecord] = []

    def record(self, record):
        existing = self.get_for_intent(record.decision.workspace_id, record.decision.intent_id)
        if existing is not None:
            return existing
        self.records.append(record)
        return record

    def get_for_intent(self, workspace_id, intent_id):
        return next(
            (
                record
                for record in self.records
                if record.decision.workspace_id == workspace_id
                and record.decision.intent_id == intent_id
            ),
            None,
        )

    def list(self, workspace_id, *, outcome, since, limit):
        return tuple(self.records)


class _ExecutionStore:
    def __init__(self):
        self.rows = {}

    def get_for_intent(self, workspace_id, intent_id):
        return self.rows.get((workspace_id, intent_id))

    def prepare(self, record):
        key = (record.order.workspace_id, record.order.intent_id)
        existing = self.rows.setdefault(key, record)
        if existing.command != record.command:
            raise PortError(ErrorCode.CONFLICT, "intent already has a different command")
        return existing

    def update(self, record):
        key = (record.order.workspace_id, record.order.intent_id)
        current = self.rows[key]
        if (
            current.command != record.command
            or record.events[:-1] != current.events
            or record.order.revision != current.order.revision + 1
        ):
            raise PortError(ErrorCode.CONFLICT, "invalid execution revision")
        self.rows[key] = record
        return record


class _LostAcknowledgementBroker(MockBroker):
    def __init__(self):
        super().__init__()
        self.lose_next_ack = True

    def submit(self, command):
        order = super().submit(command)
        if self.lose_next_ack:
            self.lose_next_ack = False
            raise PortError(ErrorCode.TIMEOUT, "simulated broker acknowledgement timeout")
        return order


class _ActiveKillSwitch:
    def is_active(self, workspace_id, account_id=None):
        return True


class _InactiveKillSwitch:
    def is_active(self, workspace_id, account_id=None):
        return False


def _intent(access: WorkspaceAccess, runtime: PaperRuntime):
    now = datetime.now(UTC)
    return OrderIntent(
        schema_version=1,
        workspace_id=access.workspace.workspace_id,
        intent_id=uuid4(),
        account_id=runtime.account_id,
        runtime_id=runtime.runtime_id,
        strategy_version_id=runtime.strategy_version_id,
        created_by=access.user_id,
        created_at=now,
        instrument_id="XSHG:600000",
        side="buy",
        order_type="market",
        quantity=Decimal("100"),
        time_in_force="day",
        trace_id=uuid4(),
    )


def _decision(intent: OrderIntent, outcome="allow"):
    reasons = (
        (
            RiskReason(
                rule_id="account.halted",
                rule_version="risk.v1",
                code="account_halted",
                summary="Account is halted.",
            ),
        )
        if outcome == "deny"
        else ()
    )
    return RiskDecision(
        schema_version=1,
        workspace_id=intent.workspace_id,
        decision_id=uuid4(),
        intent_id=intent.intent_id,
        decided_at=datetime.now(UTC),
        outcome=outcome,
        rule_set_version="risk.v1",
        input_sha256="b" * 64,
        reasons=reasons,
        trace_id=intent.trace_id,
    )


def _setup(price=Decimal("10")):
    workspace_id = uuid4()
    access = WorkspaceAccess(
        Workspace(workspace_id, "Personal", "personal", None, False, False, datetime.now(UTC)),
        uuid4(),
        Role.ADMIN,
    )
    runtime = PaperRuntime(
        workspace_id,
        uuid4(),
        uuid4(),
        uuid4(),
        StrategyVersionState.PAPER,
        True,
    )
    store = _RiskStore()
    broker = PaperBroker(lambda _workspace_id, _instrument_id: price, initial_cash=Decimal("5000"))
    return (
        access,
        runtime,
        store,
        broker,
        PaperRuntimeService(broker, RiskService(store), kill_switches=_InactiveKillSwitch()),
    )


def test_paper_execution_uses_risk_gate_shared_order_contract_and_fills():
    access, runtime, store, broker, service = _setup()
    intent = _intent(access, runtime)

    result = service.submit(
        access,
        runtime,
        intent,
        lambda order_intent: (_decision(order_intent), {"market_state": "open"}),
    )

    assert result.command is not None
    assert result.command.intent == intent
    assert result.order is not None and result.order.status == "filled"
    assert len(result.events) == 4
    assert [event.to_status for event in result.events] == [
        "created",
        "risk_approved",
        "submitted",
        "filled",
    ]
    assert result.broker_order is not None
    assert result.broker_order.average_fill_price == Decimal("10.005")
    assert result.broker_order.commission == Decimal("0.300150")
    assert len(store.records) == 1
    assert (
        broker.snapshot(access.workspace.workspace_id, runtime.account_id).positions[0].quantity
        == 100
    )


def test_active_kill_switch_persists_deny_without_calling_broker_or_evaluator():
    access, runtime, store, broker, _service = _setup()
    service = PaperRuntimeService(
        broker,
        RiskService(store),
        kill_switches=_ActiveKillSwitch(),
    )
    intent = _intent(access, runtime)

    result = service.submit(
        access,
        runtime,
        intent,
        lambda _intent: pytest.fail("risk evaluator must not authorize through the switch"),
    )

    assert result.command is None
    assert result.order is None
    assert result.decision.outcome == "deny"
    assert result.decision.reasons[0].code == "kill_switch_active"
    assert store.get_for_intent(access.workspace.workspace_id, intent.intent_id) is not None
    assert broker.snapshot(access.workspace.workspace_id, runtime.account_id).orders == ()

    retried = service.submit(
        access,
        runtime,
        intent,
        lambda _order_intent: pytest.fail("retry must return cached result"),
    )
    assert retried == result


def test_denied_risk_decision_is_recorded_without_broker_submission():
    access, runtime, store, broker, service = _setup()
    intent = _intent(access, runtime)

    result = service.submit(
        access,
        runtime,
        intent,
        lambda order_intent: (_decision(order_intent, "deny"), {"loss": "too high"}),
    )

    assert result.command is None
    assert result.order is None
    assert len(store.records) == 1
    assert broker.snapshot(access.workspace.workspace_id, runtime.account_id).orders == ()


def test_paper_broker_rejects_bad_idempotency_and_unmarketable_limit():
    access, runtime, store, broker, service = _setup()
    intent = _intent(access, runtime)
    limit_intent = intent.model_copy(update={"order_type": "limit", "limit_price": Decimal("9")})
    service.submit(
        access,
        runtime,
        limit_intent,
        lambda order_intent: (_decision(order_intent), {"price": "10"}),
    )
    snapshot = broker.snapshot(access.workspace.workspace_id, runtime.account_id)
    assert snapshot.orders[0].status == "rejected"
    assert snapshot.orders[0].rejection_code == "limit_not_marketable"

    # A runtime cannot accept an intent pinned to another Strategy Version.
    mismatched = intent.model_copy(update={"strategy_version_id": uuid4()})
    with pytest.raises(PortError) as error:
        service.submit(access, runtime, mismatched, lambda item: (_decision(item), {}))
    assert error.value.code == ErrorCode.REJECTED


def test_worker_restart_reuses_durable_command_after_lost_broker_ack():
    access, runtime, risk_store, _paper_broker, _service = _setup()
    intent = _intent(access, runtime)
    broker = _LostAcknowledgementBroker()
    executions = _ExecutionStore()
    risks = RiskService(risk_store)
    first_worker = PaperRuntimeService(
        broker, risks, executions, kill_switches=_InactiveKillSwitch()
    )
    evaluation_calls = 0

    def evaluate(order_intent):
        nonlocal evaluation_calls
        evaluation_calls += 1
        return _decision(order_intent), {"market_state": "open"}

    with pytest.raises(PortError) as error:
        first_worker.submit(access, runtime, intent, evaluate)
    assert error.value.code == ErrorCode.TIMEOUT
    prepared = executions.get_for_intent(access.workspace.workspace_id, intent.intent_id)
    assert prepared is not None and prepared.order.status == "submitted"
    broker.fill(intent.intent_id, intent.quantity, Decimal("10"))

    restarted_worker = PaperRuntimeService(
        broker, risks, executions, kill_switches=_InactiveKillSwitch()
    )
    result = restarted_worker.submit(
        access,
        runtime,
        intent,
        lambda _intent: pytest.fail("persisted risk decision must be reused"),
    )

    assert result.order is not None and result.order.status == "filled"
    assert result.command == prepared.command
    assert evaluation_calls == 1
    snapshot = broker.snapshot(access.workspace.workspace_id, runtime.account_id)
    assert len(snapshot.orders) == 1
