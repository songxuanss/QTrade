"""Safe rule evaluation with cooldown, silence, and severity escalation."""

import ast
import json
import math
import operator
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from qtrade_application.alerts import AlertEventFacts, AlertEventService, AlertRuleService
from qtrade_domain.alert_condition import validate_condition
from qtrade_domain.alert_jobs import AlertEvaluationJob
from qtrade_domain.alerts import AlertEvaluationPort, AlertSeverity, AlertSource
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.notification_delivery import NotificationDeliveryQueuePort
from qtrade_domain.ports import ErrorCode, Notification, PortError

_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
}
_ARITHMETIC: dict[type[ast.operator], Callable[[object, object], object]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def condition_matches(expression: str, facts: dict[str, object]) -> bool:
    validate_condition(expression)
    try:
        if not 1 <= len(expression) <= 512:
            raise ValueError
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 64:
            raise ValueError
        result = _value(tree.body, facts)
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError, KeyError):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid alert condition") from None
    if type(result) is not bool:
        raise PortError(ErrorCode.INVALID_INPUT, "alert condition must produce boolean")
    return result


_SENSITIVE_CONTEXT_MARKERS = (
    "password",
    "secret",
    "token",
    "credential",
    "authorization",
    "licence",
    "license",
    "api_key",
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|secret|token|authorization|api[_-]?key|licen[cs]e)\b\s*[:=]\s*([^\s,;&]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def context_snapshot_json(snapshot: dict[str, object]) -> str:
    def safe(value: object, depth: int = 0) -> object:
        if depth > 6:
            raise PortError(ErrorCode.INVALID_INPUT, "alert context is too deeply nested")
        if value is None or type(value) in {str, int, float, bool}:
            if type(value) is str:
                safe_text = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", value)
                safe_text = _BEARER_SECRET.sub("Bearer [REDACTED]", safe_text)
                return safe_text[:2048]
            if type(value) is float and not math.isfinite(value):
                raise PortError(ErrorCode.INVALID_INPUT, "alert context contains invalid number")
            return value
        if isinstance(value, dict) and len(value) <= 64:
            output: dict[str, object] = {}
            for key, child in value.items():
                if not isinstance(key, str) or not 1 <= len(key) <= 128:
                    raise PortError(ErrorCode.INVALID_INPUT, "alert context contains invalid key")
                if any(marker in key.casefold() for marker in _SENSITIVE_CONTEXT_MARKERS):
                    output[key] = "[REDACTED]"
                else:
                    output[key] = safe(child, depth + 1)
            return output
        if isinstance(value, list | tuple) and len(value) <= 64:
            return [safe(item, depth + 1) for item in value]
        raise PortError(ErrorCode.INVALID_INPUT, "alert context contains unsupported value")

    encoded = json.dumps(safe(snapshot), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded) > 65536:
        raise PortError(ErrorCode.INVALID_INPUT, "alert context exceeds size limit")
    return encoded


def _value(node: ast.AST, facts: dict[str, object]) -> object:
    if isinstance(node, ast.Constant) and type(node.value) in {str, int, float, bool}:
        return node.value
    if isinstance(node, ast.Name) and not node.id.startswith("_"):
        value = facts[node.id]
        if type(value) not in {str, int, float, bool}:
            raise TypeError
        return value
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        values = [_value(item, facts) for item in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _value(node.operand, facts)
        if type(value) is not bool:
            raise TypeError
        return not value
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITHMETIC:
        left, right = _value(node.left, facts), _value(node.right, facts)
        if type(left) not in {int, float} or type(right) not in {int, float}:
            raise TypeError
        return _ARITHMETIC[type(node.op)](left, right)
    if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators):
        left = _value(node.left, facts)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _value(comparator, facts)
            operation = _COMPARE.get(type(op))
            if operation is None or not operation(left, right):
                return False
            left = right
        return True
    raise TypeError


class AlertEvaluationService:
    def __init__(
        self,
        rules: AlertRuleService,
        events: AlertEventService,
        state: AlertEvaluationPort,
        *,
        escalation_after_seconds: int = 900,
        notification_queue: NotificationDeliveryQueuePort | None = None,
    ):
        self._rules, self._events, self._state = rules, events, state
        self._escalation_after = escalation_after_seconds
        self._notifications = notification_queue

    def evaluate(
        self,
        access: WorkspaceAccess,
        source: AlertSource,
        facts: dict[str, object],
        now: datetime,
        *,
        silenced: bool = False,
        resource_id: str | None = None,
        context_snapshot: dict[str, object] | None = None,
    ) -> int:
        access.require(Permission.READ)
        return self.evaluate_workspace(
            access.workspace.workspace_id,
            source,
            facts,
            now,
            silenced=silenced,
            resource_id=resource_id,
            context_snapshot=context_snapshot,
        )

    def evaluate_workspace(
        self,
        workspace_id: UUID,
        source: AlertSource,
        facts: dict[str, object],
        now: datetime,
        *,
        silenced: bool = False,
        resource_id: str | None = None,
        event_identity: str | None = None,
        context_snapshot: dict[str, object] | None = None,
    ) -> int:
        if now.tzinfo is None or now.utcoffset() is None or type(silenced) is not bool:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid alert evaluation context")
        emitted = 0
        for rule in self._rules.rules_for_evaluation(workspace_id):
            if not rule.enabled or rule.source != source.value:
                continue
            triggered = condition_matches(rule.condition, facts)
            severity = self._state.decision(
                workspace_id,
                rule.rule_id,
                triggered,
                now,
                rule.cooldown_seconds,
                AlertSeverity(rule.severity),
                self._escalation_after,
                silenced,
            )
            if severity is None:
                continue
            event_facts = AlertEventFacts(
                workspace_id,
                f"{rule.rule_id}:{event_identity or now.astimezone(UTC).isoformat()}",
                "rule.triggered",
                severity,
                rule.name,
                "Rule condition was satisfied.",
                now,
                resource_id,
                context_snapshot_json(
                    {
                        "rule": {
                            "rule_id": str(rule.rule_id),
                            "version": rule.version,
                            "name": rule.name,
                            "severity": rule.severity,
                        },
                        "source": source.value,
                        "occurred_at": now.astimezone(UTC).isoformat(),
                        "resource_id": resource_id,
                        "facts": facts,
                        "market_summary": (context_snapshot or {}).get("market_summary", {}),
                        "risk_summary": (context_snapshot or {}).get("risk_summary", {}),
                        "strategy_runtime": (context_snapshot or {}).get("strategy_runtime", {}),
                        "runtime_state": (context_snapshot or {}).get("runtime_state", {}),
                        "data_quality": (context_snapshot or {}).get("data_quality", {}),
                        "additional": context_snapshot or {},
                    }
                ),
            )
            if source == AlertSource.MARKET:
                event = self._events.market(event_facts)
            elif source == AlertSource.RISK:
                event = self._events.risk(event_facts)
            elif source == AlertSource.DATA:
                event = self._events.data(event_facts)
            else:
                event = self._events.system(event_facts)
            if self._notifications is not None:
                for channel, target in rule.notification_targets:
                    destination = str(workspace_id) if target == "workspace" else target
                    notification = Notification(
                        workspace_id,
                        uuid5(event.event_id, f"{channel}:{destination}"),
                        channel,
                        destination,
                        event.title,
                        event.message,
                    )
                    self._notifications.enqueue(notification, now)
            emitted += 1
        return emitted

    def evaluate_job(self, job: AlertEvaluationJob) -> int:
        snapshot = json.loads(job.context_snapshot_json)
        return self.evaluate_workspace(
            job.workspace_id,
            job.source,
            dict(job.facts),
            job.occurred_at,
            resource_id=job.resource_id,
            event_identity=str(job.job_id),
            context_snapshot=snapshot,
        )
