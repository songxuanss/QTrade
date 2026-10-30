import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from qtrade_application.alert_evaluation import AlertEvaluationService, condition_matches
from qtrade_application.alert_jobs import AlertEvaluationSubmissionService
from qtrade_application.alerts import AlertEventFacts, AlertEventService, AlertRuleService
from qtrade_domain.alert_jobs import AlertEvaluationJob
from qtrade_domain.alerts import AlertSeverity, AlertSource, AlertStatus
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.alert_evaluation import MockAlertEvaluationStore
from qtrade_mock.alert_events import MockAlertEventStore
from qtrade_mock.alert_jobs import MockAlertEvaluationQueue
from qtrade_mock.alerts import MockAlertRuleStore


def _access(role: Role) -> WorkspaceAccess:
    workspace = Workspace(uuid4(), "Personal", "personal", None, False, False, datetime.now(UTC))
    return WorkspaceAccess(workspace, uuid4(), role)


def test_alert_rules_are_workspace_scoped_and_enabled_changes_append_versions():
    access = _access(Role.ADMIN)
    other = _access(Role.ADMIN)
    service = AlertRuleService(MockAlertRuleStore())
    rule = service.create(
        access,
        name="Price threshold",
        source="market",
        condition="price > 100",
        severity="warning",
        cooldown_seconds=3600,
    )

    disabled = service.set_enabled(access, rule.rule_id, False)

    assert disabled.version == 2
    assert not disabled.enabled
    assert [item.version for item in service.versions(access, rule.rule_id)] == [1, 2]
    assert service.list(other) == ()


def test_alert_rule_delivery_targets_are_versioned_and_notifications_use_each_target():
    access = _access(Role.ADMIN)
    rules = AlertRuleService(MockAlertRuleStore())
    events = AlertEventService(MockAlertEventStore())
    targets = (("email", "risk@example.com"), ("webhook", "risk-critical"))
    rule = rules.create(
        access,
        name="Risk limit",
        source="risk",
        condition="loss_pct > 5",
        severity="critical",
        cooldown_seconds=30,
        notification_targets=targets,
    )
    assert rule.notification_targets == targets

    class CapturingQueue:
        def __init__(self):
            self.notifications = []

        def enqueue(self, notification, now):
            self.notifications.append((notification, now))

    outbox = CapturingQueue()
    evaluator = AlertEvaluationService(
        rules,
        events,
        MockAlertEvaluationStore(),
        notification_queue=outbox,
    )
    now = datetime.now(UTC)
    assert (
        evaluator.evaluate(
            access,
            AlertSource.RISK,
            {"loss_pct": 6},
            now,
            context_snapshot={
                "market_summary": {"last_price": 10.5},
                "risk_summary": {"loss_pct": 6, "access_token": "private-token"},
                "strategy_runtime": {"mode": "paper", "state": "running"},
                "runtime_state": {"worker": "ready"},
                "data_quality": {"missing_count": 2, "duplicate_count": 0},
                "error_detail": "webhook token=private-token failed",
            },
        )
        == 1
    )
    assert [item.channel for item, _ in outbox.notifications] == ["email", "webhook"]
    assert [item.destination_id for item, _ in outbox.notifications] == [
        "risk@example.com",
        "risk-critical",
    ]
    event = events.list(access, 10)[0]
    snapshot = json.loads(event.context_snapshot_json)
    assert snapshot["rule"]["version"] == rule.version
    assert snapshot["risk_summary"] == {"access_token": "[REDACTED]", "loss_pct": 6}
    assert snapshot["strategy_runtime"] == {"mode": "paper", "state": "running"}
    assert snapshot["market_summary"] == {"last_price": 10.5}
    assert snapshot["runtime_state"] == {"worker": "ready"}
    assert snapshot["data_quality"] == {"duplicate_count": 0, "missing_count": 2}
    assert snapshot["additional"]["error_detail"] == "webhook token=[REDACTED] failed"


def test_four_alert_sources_share_workspace_scoped_contract_and_status():
    store = MockAlertEventStore()
    service = AlertEventService(store)
    workspace_id = uuid4()
    occurred_at = datetime.now(UTC)
    facts = AlertEventFacts(
        workspace_id,
        "source-event-1",
        "health.degraded",
        AlertSeverity.WARNING,
        "Feed delayed",
        "Market feed is delayed",
        occurred_at,
        "MOCK:ABC",
    )
    events = (
        service.market(facts),
        service.risk(facts),
        service.data(facts),
        service.system(facts),
    )

    assert tuple(event.source for event in events) == tuple(AlertSource)
    assert all(event.status == AlertStatus.OPEN for event in events)
    assert all(event.workspace_id == workspace_id for event in events)
    assert len(store.list(workspace_id, 20)) == 4


def test_alert_event_source_identity_is_idempotent_and_conflicts_on_changed_facts():
    store = MockAlertEventStore()
    service = AlertEventService(store)
    facts = AlertEventFacts(
        uuid4(),
        "provider-1",
        "market.gap",
        AlertSeverity.INFO,
        "Gap",
        "Missing interval",
        datetime.now(UTC),
    )
    first = service.market(facts)
    retried = service.market(facts)
    assert retried.event_id == first.event_id
    changed = AlertEventFacts(
        facts.workspace_id,
        facts.source_event_id,
        facts.event_type,
        facts.severity,
        facts.title,
        "Changed facts",
        facts.occurred_at,
    )
    with pytest.raises(PortError) as error:
        service.market(changed)
    assert error.value.code == ErrorCode.CONFLICT


def test_alert_disposition_lifecycle_is_workspace_scoped_and_permission_checked():
    access = _access(Role.ADMIN)
    viewer = WorkspaceAccess(access.workspace, uuid4(), Role.VIEWER)
    store = MockAlertEventStore()
    service = AlertEventService(store)
    event = service.market(
        AlertEventFacts(
            access.workspace.workspace_id,
            "lifecycle-1",
            "market.gap",
            AlertSeverity.CRITICAL,
            "Feed gap",
            "A price interval is missing",
            datetime.now(UTC),
            "600519.SH",
            '{"rule":{"version":2}}',
        )
    )
    with pytest.raises(PortError) as denied:
        service.handle(viewer, event.event_id, "claim", now=datetime.now(UTC))
    assert denied.value.code == ErrorCode.REJECTED
    now = datetime.now(UTC)
    claimed = service.handle(access, event.event_id, "claim", now=now)
    assert claimed.assignee_id == access.user_id
    acknowledged = service.handle(
        access, event.event_id, "acknowledge", now=now + timedelta(seconds=1)
    )
    assert acknowledged.status == AlertStatus.ACKNOWLEDGED
    silence_until = now + timedelta(hours=1)
    silenced = service.handle(
        access, event.event_id, "silence", now=now, silenced_until=silence_until
    )
    assert silenced.silenced_until == silence_until
    noted = service.handle(
        access, event.event_id, "note", now=now + timedelta(seconds=2), note="Investigating"
    )
    assert noted.context_snapshot_json == event.context_snapshot_json
    closed = service.handle(access, event.event_id, "close", now=now + timedelta(seconds=3))
    assert closed.status == AlertStatus.CLOSED
    assert [item.action for item in service.history(access, event.event_id)] == [
        "claim",
        "acknowledge",
        "silence",
        "note",
        "close",
    ]
    with pytest.raises(PortError) as conflict:
        service.handle(access, event.event_id, "acknowledge", now=now + timedelta(seconds=4))
    assert conflict.value.code == ErrorCode.CONFLICT
    with pytest.raises(PortError) as missing:
        service.detail(_access(Role.ADMIN), event.event_id)
    assert missing.value.code == ErrorCode.NOT_FOUND


def test_evaluator_respects_cooldown_and_escalates_silenced_warning_to_critical():
    access = _access(Role.ADMIN)
    event_store = MockAlertEventStore()
    rules = AlertRuleService(MockAlertRuleStore())
    events = AlertEventService(event_store)
    now = datetime.now(UTC)
    rules.create(
        access,
        name="Feed latency",
        source="market",
        condition="lag_ms > 1000 and online == True",
        severity="warning",
        cooldown_seconds=60,
    )
    evaluator = AlertEvaluationService(
        rules, events, MockAlertEvaluationStore(), escalation_after_seconds=120
    )
    facts = {"lag_ms": 1500, "online": True}

    assert evaluator.evaluate(access, AlertSource.MARKET, facts, now) == 1
    assert evaluator.evaluate(access, AlertSource.MARKET, facts, now + timedelta(seconds=10)) == 0
    assert evaluator.evaluate(access, AlertSource.MARKET, facts, now + timedelta(seconds=120)) == 1
    assert evaluator.evaluate(access, AlertSource.MARKET, facts, now + timedelta(seconds=130)) == 0
    alerts = event_store.list(access.workspace.workspace_id, 20)
    assert len(alerts) == 2
    assert alerts[0].severity == AlertSeverity.CRITICAL


def test_escalated_critical_alert_bypasses_silence_and_existing_cooldown():
    access = _access(Role.ADMIN)
    event_store = MockAlertEventStore()
    rules = AlertRuleService(MockAlertRuleStore())
    events = AlertEventService(event_store)
    now = datetime.now(UTC)
    rules.create(
        access,
        name="Risk threshold",
        source="risk",
        condition="loss_pct > 5",
        severity="warning",
        cooldown_seconds=3600,
    )
    evaluator = AlertEvaluationService(
        rules, events, MockAlertEvaluationStore(), escalation_after_seconds=60
    )
    facts = {"loss_pct": 6}
    assert evaluator.evaluate(access, AlertSource.RISK, facts, now) == 1
    assert (
        evaluator.evaluate(
            access, AlertSource.RISK, facts, now + timedelta(seconds=60), silenced=True
        )
        == 1
    )
    alerts = event_store.list(access.workspace.workspace_id, 20)
    assert [event.severity for event in alerts] == [AlertSeverity.CRITICAL, AlertSeverity.WARNING]


@pytest.mark.parametrize(
    "expression",
    ["__import__('os')", "price > 1; 1 == 1", "price ** 9 > 1", "unknown > 1"],
)
def test_alert_condition_evaluator_rejects_unsafe_or_unknown_expressions(expression):
    with pytest.raises(PortError) as error:
        condition_matches(expression, {"price": 10})
    assert error.value.code == ErrorCode.INVALID_INPUT


def test_alert_evaluation_submission_requires_write_permission_and_uses_fenced_queue():
    queue = MockAlertEvaluationQueue()
    service = AlertEvaluationSubmissionService(queue)
    access = _access(Role.ADMIN)
    now = datetime.now(UTC)
    job = service.submit(
        access,
        AlertSource.MARKET,
        {"price": 101, "online": True},
        occurred_at=now,
        resource_id="600519.SH",
        context_snapshot={"market_summary": {"last_price": 101, "api_token": "must-not-persist"}},
    )
    assert isinstance(job, AlertEvaluationJob)
    assert job.facts == (("online", True), ("price", 101))
    assert json.loads(job.context_snapshot_json) == {
        "market_summary": {"api_token": "[REDACTED]", "last_price": 101}
    }
    lease = queue.ready(now, 10, 30)[0]
    assert lease.job == job
    assert queue.ready(now, 10, 30) == ()
    stale_lease = type(lease)(lease.job, uuid4(), lease.attempts)
    assert not queue.finish(stale_lease, now, succeeded=True)
    assert queue.finish(lease, now, succeeded=True)
    assert queue.ready(now, 10, 30) == ()

    viewer = _access(Role.VIEWER)
    with pytest.raises(PortError) as error:
        service.submit(viewer, AlertSource.MARKET, {"price": 101}, occurred_at=now)
    assert error.value.code == ErrorCode.REJECTED
    with pytest.raises(PortError) as secret_fact:
        service.submit(access, AlertSource.MARKET, {"access_token": "private"}, occurred_at=now)
    assert secret_fact.value.code == ErrorCode.INVALID_INPUT


def test_viewer_cannot_create_or_change_alert_rules():
    service = AlertRuleService(MockAlertRuleStore())
    viewer = _access(Role.VIEWER)
    with pytest.raises(PortError) as error:
        service.create(
            viewer,
            name="Price threshold",
            source="market",
            condition="price > 100",
            severity="warning",
            cooldown_seconds=60,
        )
    assert error.value.code == ErrorCode.REJECTED


@pytest.mark.parametrize(
    "values",
    [
        {"source": "unknown"},
        {"severity": "urgent"},
        {"condition": ""},
        {"condition": "price >"},
        {"condition": "price"},
        {"condition": '__import__("os") == 1'},
        {"condition": "price.real > 1"},
        {"cooldown_seconds": -1},
        {"cooldown_seconds": True},
        {"name": " "},
    ],
)
def test_alert_rule_rejects_invalid_parameters(values):
    now = datetime.now(UTC)
    defaults = {
        "rule_id": uuid4(),
        "workspace_id": uuid4(),
        "version": 1,
        "name": "Price threshold",
        "source": "market",
        "condition": "price > 100",
        "severity": "warning",
        "cooldown_seconds": 60,
        "enabled": True,
        "created_at": now,
    }
    defaults.update(values)
    from qtrade_domain.alerts import AlertRuleVersion

    with pytest.raises(PortError):
        AlertRuleVersion(**defaults)
