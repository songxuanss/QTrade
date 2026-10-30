"""Thread-safe in-memory alert rule adapter for local development and tests."""

from threading import RLock
from uuid import UUID

from qtrade_domain.alerts import AlertRuleVersion
from qtrade_domain.ports import ErrorCode, PortError


class MockAlertRuleStore:
    def __init__(self) -> None:
        self._rules: dict[tuple[UUID, UUID], list[AlertRuleVersion]] = {}
        self._lock = RLock()

    def create(self, rule: AlertRuleVersion) -> AlertRuleVersion:
        key = (rule.workspace_id, rule.rule_id)
        with self._lock:
            if key in self._rules:
                raise PortError(ErrorCode.CONFLICT, "alert rule already exists")
            self._rules[key] = [rule]
        return rule

    def current(self, workspace_id: UUID, rule_id: UUID) -> AlertRuleVersion | None:
        with self._lock:
            versions = self._rules.get((workspace_id, rule_id))
            return versions[-1] if versions else None

    def append(self, rule: AlertRuleVersion) -> AlertRuleVersion:
        key = (rule.workspace_id, rule.rule_id)
        with self._lock:
            versions = self._rules.get(key)
            if not versions:
                raise PortError(ErrorCode.NOT_FOUND, "alert rule unavailable")
            if rule.version != versions[-1].version + 1:
                raise PortError(ErrorCode.CONFLICT, "alert rule version changed")
            versions.append(rule)
        return rule

    def list(self, workspace_id: UUID) -> tuple[AlertRuleVersion, ...]:
        with self._lock:
            return tuple(
                versions[-1]
                for (owner, _), versions in sorted(
                    self._rules.items(), key=lambda pair: str(pair[0])
                )
                if owner == workspace_id
            )

    def versions(self, workspace_id: UUID, rule_id: UUID) -> tuple[AlertRuleVersion, ...]:
        with self._lock:
            return tuple(self._rules.get((workspace_id, rule_id), ()))
