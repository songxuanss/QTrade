"""Deterministic, explainable pre-trade risk evaluation."""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import UUID

from qtrade_contracts import OrderIntent, RiskDecision, RiskReason
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.risk_limits import LimitUsage


@dataclass(frozen=True)
class RiskPolicy:
    policy_id: str
    version: str
    max_price_deviation: Decimal
    quantity_step: Decimal
    max_orders_per_window: int
    max_position_quantity: Decimal
    max_exposure: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal

    def __post_init__(self) -> None:
        decimals = (
            self.max_price_deviation,
            self.quantity_step,
            self.max_position_quantity,
            self.max_exposure,
            self.max_daily_loss,
            self.max_drawdown,
        )
        if (
            not isinstance(self.policy_id, str)
            or not 1 <= len(self.policy_id) <= 128
            or not isinstance(self.version, str)
            or not 1 <= len(self.version) <= 128
            or any(
                not isinstance(value, Decimal) or not value.is_finite() or value < 0
                for value in decimals
            )
            or self.max_price_deviation >= 1
            or self.quantity_step == 0
            or type(self.max_orders_per_window) is not int
            or self.max_orders_per_window < 1
            or self.max_position_quantity == 0
            or self.max_exposure == 0
            or self.max_daily_loss == 0
            or self.max_drawdown >= 1
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid risk policy")


@dataclass(frozen=True)
class RiskCheckContext:
    intent: OrderIntent
    actor_authorized: bool
    strategy_approved: bool
    runtime_active: bool
    market_open: bool
    instrument_enabled: bool
    planned_price: Decimal
    reference_price: Decimal
    orders_in_window: int
    current_position_quantity: Decimal
    current_exposure: Decimal
    daily_loss: Decimal
    current_drawdown: Decimal
    kill_switch_active: bool
    existing_intent_ids: tuple[UUID, ...] = ()
    requires_approval: bool = False
    limit_usages: tuple[LimitUsage, ...] = ()

    def __post_init__(self) -> None:
        decimals = (
            self.planned_price,
            self.reference_price,
            self.current_position_quantity,
            self.current_exposure,
            self.daily_loss,
            self.current_drawdown,
        )
        if (
            not isinstance(self.intent, OrderIntent)
            or any(not isinstance(value, Decimal) or not value.is_finite() for value in decimals)
            or self.planned_price <= 0
            or self.reference_price <= 0
            or self.current_exposure < 0
            or self.daily_loss < 0
            or not 0 <= self.current_drawdown <= 1
            or type(self.orders_in_window) is not int
            or self.orders_in_window < 0
            or not isinstance(self.existing_intent_ids, tuple)
            or not isinstance(self.limit_usages, tuple)
            or any(not isinstance(value, LimitUsage) for value in self.limit_usages)
            or any(not isinstance(value, UUID) for value in self.existing_intent_ids)
            or any(
                type(value) is not bool
                for value in (
                    self.actor_authorized,
                    self.strategy_approved,
                    self.runtime_active,
                    self.market_open,
                    self.instrument_enabled,
                    self.kill_switch_active,
                    self.requires_approval,
                )
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid risk check context")
        limit_keys = tuple(
            (usage.limit.dimension, usage.limit.scope_id, usage.limit.metric)
            for usage in self.limit_usages
        )
        limit_ids = tuple(usage.limit.limit_id for usage in self.limit_usages)
        if len(set(limit_keys)) != len(limit_keys) or len(set(limit_ids)) != len(limit_ids):
            raise PortError(ErrorCode.INVALID_INPUT, "duplicate risk limit usage")
        object.__setattr__(
            self,
            "limit_usages",
            tuple(
                sorted(
                    self.limit_usages,
                    key=lambda usage: (
                        usage.limit.dimension,
                        usage.limit.scope_id,
                        usage.limit.metric,
                        usage.limit.limit_id,
                    ),
                )
            ),
        )


def evaluate_order_intent(
    policy: RiskPolicy,
    context: RiskCheckContext,
    *,
    decided_at: datetime,
    decision_id: UUID,
) -> RiskDecision:
    """Apply all applicable pre-trade controls in stable order and hash their inputs."""
    if decided_at.tzinfo is None or decided_at.utcoffset() is None:
        raise PortError(ErrorCode.INVALID_INPUT, "risk decision time must be timezone-aware")
    intent = context.intent
    reasons: list[RiskReason] = []

    def deny(
        code: str,
        summary: str,
        current: Decimal | None = None,
        limit: Decimal | None = None,
        *,
        rule_id: str | None = None,
        rule_version: str | None = None,
    ) -> None:
        reasons.append(
            RiskReason(
                rule_id=rule_id or policy.policy_id,
                rule_version=rule_version or policy.version,
                code=code,
                summary=summary,
                current_value=current,
                limit_value=limit,
            )
        )

    if not context.actor_authorized:
        deny("actor_unauthorized", "当前用户无权提交此 Order Intent。")
    if not context.strategy_approved:
        deny("strategy_unapproved", "策略版本未达到当前运行所需状态。")
    if not context.runtime_active:
        deny("runtime_inactive", "策略运行时未处于活动状态。")
    if not context.market_open:
        deny("market_closed", "目标市场当前不允许提交新订单。")
    if not context.instrument_enabled:
        deny("instrument_disabled", "标的未获准用于该 Workspace 的交易。")
    if context.kill_switch_active:
        deny("kill_switch_active", "Kill Switch 已启用，拒绝新的 Order Command。")

    deviation = abs(context.planned_price - context.reference_price) / context.reference_price
    if deviation > policy.max_price_deviation:
        deny(
            "price_deviation",
            "委托价格偏离参考价格超过限额。",
            deviation,
            policy.max_price_deviation,
        )
    if intent.quantity % policy.quantity_step != 0:
        deny(
            "quantity_precision",
            "委托数量不符合标的最小交易单位。",
            intent.quantity % policy.quantity_step,
            Decimal(0),
        )
    if intent.intent_id in context.existing_intent_ids:
        deny("duplicate_intent", "该 Order Intent 已经处理过。")
    if context.orders_in_window >= policy.max_orders_per_window:
        deny(
            "order_frequency",
            "当前订单频率已达到限额。",
            Decimal(context.orders_in_window),
            Decimal(policy.max_orders_per_window),
        )

    signed_quantity = intent.quantity if intent.side == "buy" else -intent.quantity
    resulting_position = context.current_position_quantity + signed_quantity
    if abs(resulting_position) > policy.max_position_quantity:
        deny(
            "position_limit",
            "订单将导致标的持仓数量超过限额。",
            abs(resulting_position),
            policy.max_position_quantity,
        )
    order_notional = intent.quantity * context.planned_price
    closing_quantity = (
        min(
            intent.quantity,
            abs(context.current_position_quantity),
        )
        if (
            (intent.side == "sell" and context.current_position_quantity > 0)
            or (intent.side == "buy" and context.current_position_quantity < 0)
        )
        else Decimal(0)
    )
    exposure_change = order_notional - closing_quantity * context.planned_price
    resulting_exposure = max(Decimal(0), context.current_exposure + exposure_change)
    if resulting_exposure > policy.max_exposure:
        deny(
            "exposure_limit",
            "订单将导致名义暴露超过限额。",
            resulting_exposure,
            policy.max_exposure,
        )
    if context.daily_loss >= policy.max_daily_loss:
        deny(
            "daily_loss_limit",
            "账户单日亏损已达到限额。",
            context.daily_loss,
            policy.max_daily_loss,
        )
    if context.current_drawdown >= policy.max_drawdown:
        deny(
            "drawdown_limit",
            "账户回撤已达到限额。",
            context.current_drawdown,
            policy.max_drawdown,
        )
    for usage in context.limit_usages:
        if usage.breached:
            deny(
                f"limit_exceeded.{usage.limit.dimension}.{usage.limit.metric}",
                f"{usage.limit.dimension} 限额 {usage.limit.scope_id} 将达到 "
                f"{usage.projected_value}，"
                f"超过上限 {usage.limit.maximum}。",
                usage.projected_value,
                usage.limit.maximum,
                rule_id=usage.limit.limit_id,
                rule_version=usage.limit.version,
            )

    input_json = json.dumps(
        {
            "context": {
                "actor_authorized": context.actor_authorized,
                "current_drawdown": str(context.current_drawdown),
                "current_exposure": str(context.current_exposure),
                "current_position_quantity": str(context.current_position_quantity),
                "daily_loss": str(context.daily_loss),
                "existing_intent_ids": sorted(map(str, context.existing_intent_ids)),
                "instrument_enabled": context.instrument_enabled,
                "intent": intent.model_dump(mode="json"),
                "kill_switch_active": context.kill_switch_active,
                "limit_usages": [
                    {
                        "breached": usage.breached,
                        "current_value": str(usage.current_value),
                        "dimension": usage.limit.dimension,
                        "limit_id": usage.limit.limit_id,
                        "limit_maximum": str(usage.limit.maximum),
                        "limit_metric": usage.limit.metric,
                        "limit_scope_id": usage.limit.scope_id,
                        "limit_version": usage.limit.version,
                        "projected_value": str(usage.projected_value),
                        "utilization": str(usage.utilization),
                    }
                    for usage in context.limit_usages
                ],
                "market_open": context.market_open,
                "orders_in_window": context.orders_in_window,
                "planned_price": str(context.planned_price),
                "reference_price": str(context.reference_price),
                "requires_approval": context.requires_approval,
                "runtime_active": context.runtime_active,
                "strategy_approved": context.strategy_approved,
            },
            "policy": {
                "id": policy.policy_id,
                "version": policy.version,
                "max_daily_loss": str(policy.max_daily_loss),
                "max_drawdown": str(policy.max_drawdown),
                "max_exposure": str(policy.max_exposure),
                "max_order_frequency": policy.max_orders_per_window,
                "max_position_quantity": str(policy.max_position_quantity),
                "max_price_deviation": str(policy.max_price_deviation),
                "quantity_step": str(policy.quantity_step),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    outcome: Literal["allow", "deny", "require_approval"] = (
        "deny" if reasons else "require_approval" if context.requires_approval else "allow"
    )
    if outcome == "require_approval":
        reasons.append(
            RiskReason(
                rule_id=policy.policy_id,
                rule_version=policy.version,
                code="approval_required",
                summary="该订单需要授权用户审批后才能生成 Order Command。",
            )
        )
    return RiskDecision(
        schema_version=1,
        workspace_id=intent.workspace_id,
        decision_id=decision_id,
        intent_id=intent.intent_id,
        decided_at=decided_at,
        outcome=outcome,
        rule_set_version=policy.version,
        input_sha256=sha256(input_json.encode()).hexdigest(),
        reasons=tuple(reasons),
        trace_id=intent.trace_id,
    )
