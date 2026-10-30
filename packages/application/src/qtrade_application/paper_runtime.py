"""Paper execution use case; the same risk decision contract gates every order."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from qtrade_application.risk import RiskService
from qtrade_contracts import (
    Order,
    OrderCommand,
    OrderEvent,
    OrderIntent,
    OrderStatus,
    RiskDecision,
    RiskReason,
)
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.kill_switch import KillSwitchStore
from qtrade_domain.order_execution import OrderExecutionRecord, OrderExecutionStore
from qtrade_domain.orders import transition_order
from qtrade_domain.ports import (
    BrokerOrder,
    BrokerOrderStatus,
    BrokerPort,
    ErrorCode,
    PortError,
)
from qtrade_domain.risk_records import RiskDecisionRecord
from qtrade_domain.strategies import StrategyVersionState


class RiskEvaluator(Protocol):
    def __call__(self, intent: OrderIntent) -> tuple[RiskDecision, dict[str, object]]: ...


@dataclass(frozen=True)
class PaperRuntime:
    workspace_id: UUID
    runtime_id: UUID
    account_id: UUID
    strategy_version_id: UUID
    strategy_state: StrategyVersionState
    active: bool


@dataclass(frozen=True)
class PaperExecutionResult:
    decision: RiskDecision
    command: OrderCommand | None
    order: Order | None
    broker_order: BrokerOrder | None
    events: tuple[OrderEvent, ...]


class PaperRuntimeService:
    def __init__(
        self,
        broker: BrokerPort,
        risks: RiskService,
        executions: OrderExecutionStore | None = None,
        *,
        kill_switches: KillSwitchStore,
    ):
        self._broker = broker
        self._risks = risks
        self._executions = executions
        self._kill_switches = kill_switches
        self._results: dict[UUID, tuple[OrderIntent, PaperExecutionResult]] = {}

    def submit(
        self,
        access: WorkspaceAccess,
        runtime: PaperRuntime,
        intent: OrderIntent,
        evaluate_risk: RiskEvaluator,
    ) -> PaperExecutionResult:
        access.require(Permission.PAPER_START, object_workspace_id=runtime.workspace_id)
        if (
            not isinstance(runtime, PaperRuntime)
            or runtime.workspace_id != access.workspace.workspace_id
            or not runtime.active
            or runtime.strategy_state != StrategyVersionState.PAPER
            or intent.workspace_id != runtime.workspace_id
            or intent.runtime_id != runtime.runtime_id
            or intent.account_id != runtime.account_id
            or intent.strategy_version_id != runtime.strategy_version_id
        ):
            raise PortError(ErrorCode.REJECTED, "Paper Runtime is unavailable for this intent")
        if self._executions is not None:
            stored = self._executions.get_for_intent(intent.workspace_id, intent.intent_id)
            if stored is not None:
                if stored.command.intent != intent:
                    raise PortError(ErrorCode.CONFLICT, "Paper intent identity reused")
                if stored.order.status in {"filled", "rejected", "cancelled"}:
                    return self._result(stored)
                return self._recover(stored)
        cached = self._results.get(intent.intent_id)
        if cached is not None:
            if cached[0] != intent:
                raise PortError(ErrorCode.CONFLICT, "Paper intent identity reused")
            return cached[1]

        if self._kill_switches.is_active(intent.workspace_id, intent.account_id):
            decided_at = datetime.now(UTC)
            decision = RiskDecision(
                schema_version=1,
                workspace_id=intent.workspace_id,
                decision_id=uuid4(),
                intent_id=intent.intent_id,
                decided_at=decided_at,
                outcome="deny",
                rule_set_version="kill-switch.v1",
                input_sha256=sha256(
                    f"{intent.workspace_id}:{intent.account_id}:active".encode()
                ).hexdigest(),
                reasons=(
                    RiskReason(
                        rule_id="kill-switch",
                        rule_version="1",
                        code="kill_switch_active",
                        summary="Kill Switch 已启用，拒绝新的 Order Command。",
                    ),
                ),
                trace_id=intent.trace_id,
            )
            record = self._risks.record(
                RiskDecisionRecord(
                    decision,
                    json.dumps(
                        {"account_id": str(intent.account_id), "kill_switch_active": True},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
            result = PaperExecutionResult(record.decision, None, None, None, ())
            self._results[intent.intent_id] = (intent, result)
            return result

        persisted_risk = self._risks.decision_for_intent(intent.workspace_id, intent.intent_id)
        if persisted_risk is None:
            decision, input_summary = evaluate_risk(intent)
            if (
                decision.workspace_id != intent.workspace_id
                or decision.intent_id != intent.intent_id
                or decision.trace_id != intent.trace_id
            ):
                raise PortError(
                    ErrorCode.INVALID_INPUT, "risk decision does not match Paper intent"
                )
            persisted_risk = self._risks.record(
                RiskDecisionRecord(
                    decision,
                    json.dumps(input_summary, separators=(",", ":"), sort_keys=True),
                )
            )
        decision = persisted_risk.decision
        if (
            decision.workspace_id != intent.workspace_id
            or decision.intent_id != intent.intent_id
            or decision.trace_id != intent.trace_id
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "risk decision does not match Paper intent")
        if decision.outcome != "allow":
            result = PaperExecutionResult(decision, None, None, None, ())
            self._results[intent.intent_id] = (intent, result)
            return result

        authorized_at = decision.decided_at
        if (
            authorized_at.tzinfo is None
            or authorized_at.utcoffset() is None
            or authorized_at < intent.created_at
        ):
            raise PortError(
                ErrorCode.INVALID_INPUT,
                "authorization time must be timezone-aware and follow risk evaluation",
            )
        command = OrderCommand(
            schema_version=1,
            workspace_id=intent.workspace_id,
            command_id=uuid5(intent.workspace_id, f"command:{intent.intent_id}"),
            idempotency_key=intent.intent_id,
            intent=intent,
            decision=decision,
            authorized_at=authorized_at,
        )
        order = Order(
            schema_version=1,
            workspace_id=intent.workspace_id,
            order_id=uuid5(intent.workspace_id, f"order:{intent.intent_id}"),
            account_id=intent.account_id,
            runtime_id=intent.runtime_id,
            intent_id=intent.intent_id,
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            instrument_id=intent.instrument_id,
            side=intent.side,
            status="created",
            quantity=intent.quantity,
            created_at=intent.created_at,
            updated_at=intent.created_at,
            revision=1,
        )
        created_event = OrderEvent(
            schema_version=1,
            workspace_id=intent.workspace_id,
            event_id=uuid4(),
            order_id=order.order_id,
            revision=1,
            from_status=None,
            to_status="created",
            occurred_at=intent.created_at,
            source="system",
            filled_quantity=order.filled_quantity,
        )
        order, approved_event = transition_order(
            order,
            "risk_approved",
            occurred_at=authorized_at,
            source="risk",
            event_id=uuid4(),
        )
        order, submitted_event = transition_order(
            order,
            "submitted",
            occurred_at=authorized_at,
            source="system",
            event_id=uuid4(),
        )
        execution = OrderExecutionRecord(
            command,
            order,
            (created_event, approved_event, submitted_event),
        )
        if self._executions is not None:
            execution = self._executions.prepare(execution)
        result = self._recover(execution)
        self._results[intent.intent_id] = (intent, result)
        return result

    def _recover(self, execution: OrderExecutionRecord) -> PaperExecutionResult:
        broker_order = self._broker.submit(execution.command)
        status_by_broker: dict[BrokerOrderStatus, OrderStatus | None] = {
            "submitted": None,
            "partially_filled": "partially_filled",
            "filled": "filled",
            "cancelled": "cancelled",
            "rejected": "rejected",
        }
        target = status_by_broker[broker_order.status]
        if target is None:
            return self._result(execution)
        if (
            execution.order.status == target
            and execution.order.filled_quantity == broker_order.filled_quantity
            and execution.order.average_fill_price == broker_order.average_fill_price
        ):
            return self._result(execution)
        order, event = transition_order(
            execution.order,
            target,
            occurred_at=max(execution.order.updated_at, datetime.now(UTC)),
            source="broker",
            event_id=uuid4(),
            filled_quantity=broker_order.filled_quantity,
            average_fill_price=broker_order.average_fill_price,
            reason=broker_order.rejection_code,
        )
        updated = OrderExecutionRecord(
            execution.command,
            order,
            (*execution.events, event),
            broker_order,
        )
        if self._executions is not None:
            updated = self._executions.update(updated)
        return self._result(updated)

    @staticmethod
    def _result(execution: OrderExecutionRecord) -> PaperExecutionResult:
        return PaperExecutionResult(
            execution.command.decision,
            execution.command,
            execution.order,
            execution.broker_order,
            execution.events,
        )
