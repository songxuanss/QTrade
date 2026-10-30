from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from qtrade_application.alerts import AlertEventService
from qtrade_application.reconciliation import ReconciliationService
from qtrade_domain.alerts import AlertSource
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import BrokerOrder, BrokerPosition, BrokerSnapshot, PortError
from qtrade_domain.reconciliation import (
    LocalAccountSnapshot,
    ReconciliationDifference,
    ReconciliationReport,
    ReconciliationTolerance,
    reconcile_account,
)
from qtrade_mock.alert_events import MockAlertEventStore


class _Broker:
    def __init__(self, snapshot):
        self.value = snapshot

    def snapshot(self, workspace_id, account_id):
        assert self.value.workspace_id == workspace_id
        assert self.value.account_id == account_id
        return self.value


class _Local:
    def __init__(self, snapshot):
        self.value = snapshot

    def snapshot(self, workspace_id, account_id):
        assert self.value.workspace_id == workspace_id
        assert self.value.account_id == account_id
        return self.value


class _Reports:
    def __init__(self):
        self.rows = []

    def record(self, report):
        self.rows.append(report)
        return report

    def latest(self, workspace_id, account_id):
        return next(
            (
                report
                for report in reversed(self.rows)
                if report.workspace_id == workspace_id and report.account_id == account_id
            ),
            None,
        )


class _Gates:
    def __init__(self):
        self.rows = {}

    def block(self, workspace_id, account_id, report_id, occurred_at):
        self.rows[(workspace_id, account_id)] = (report_id, occurred_at)

    def is_blocked(self, workspace_id, account_id):
        return (workspace_id, account_id) in self.rows


def _snapshots(mismatch: bool):
    workspace_id, account_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    local_order = BrokerOrder(
        workspace_id,
        account_id,
        uuid4(),
        "broker-order-1",
        "partially_filled",
        Decimal("10"),
        Decimal("4"),
        average_fill_price=Decimal("10.2"),
    )
    broker_order = BrokerOrder(
        workspace_id,
        account_id,
        local_order.command_id,
        local_order.broker_order_id,
        "filled" if mismatch else "partially_filled",
        Decimal("10"),
        Decimal("10" if mismatch else "4"),
        average_fill_price=Decimal("10.3" if mismatch else "10.2"),
    )
    local = LocalAccountSnapshot(
        workspace_id,
        account_id,
        now,
        "CNY",
        Decimal("90000"),
        (BrokerPosition("XSHG:600000", Decimal("10"), Decimal("10")),),
        (local_order,),
    )
    broker = BrokerSnapshot(
        workspace_id,
        account_id,
        now,
        "CNY",
        Decimal("95000" if mismatch else "90000"),
        (
            BrokerPosition(
                "XSHG:600000",
                Decimal("8" if mismatch else "10"),
                Decimal("10"),
            ),
        ),
        (broker_order,),
    )
    access = WorkspaceAccess(
        Workspace(workspace_id, "Personal", "personal", None, False, False, now),
        uuid4(),
        Role.RISK_MANAGER,
    )
    return access, account_id, local, broker, now


def test_reconciliation_mismatch_blocks_live_account_and_emits_risk_alert():
    access, account_id, local, broker_snapshot, now = _snapshots(mismatch=True)
    reports, gates, alert_store = _Reports(), _Gates(), MockAlertEventStore()
    service = ReconciliationService(
        _Broker(broker_snapshot),
        _Local(local),
        reports,
        gates,
        AlertEventService(alert_store),
    )

    report = service.reconcile(
        access,
        account_id,
        tolerance=ReconciliationTolerance(Decimal("0"), Decimal("0"), Decimal("0")),
        now=now,
    )

    assert report.status == "mismatch"
    assert {item.category for item in report.differences} == {"cash", "position", "fill"}
    assert gates.is_blocked(access.workspace.workspace_id, account_id)
    events = alert_store.list(access.workspace.workspace_id, 10)
    assert len(events) == 1
    assert events[0].source == AlertSource.RISK
    assert events[0].severity.value == "critical"


def test_reconciliation_match_neither_blocks_nor_emits_alert():
    access, account_id, local, broker_snapshot, now = _snapshots(mismatch=False)
    reports, gates, alert_store = _Reports(), _Gates(), MockAlertEventStore()
    service = ReconciliationService(
        _Broker(broker_snapshot),
        _Local(local),
        reports,
        gates,
        AlertEventService(alert_store),
    )

    report = service.reconcile(
        access,
        account_id,
        tolerance=ReconciliationTolerance(Decimal("0"), Decimal("0"), Decimal("0")),
        now=now,
    )

    assert report.status == "matched"
    assert report.differences == ()
    assert not gates.is_blocked(access.workspace.workspace_id, account_id)
    assert alert_store.list(access.workspace.workspace_id, 10) == ()


def test_reconciliation_reports_cannot_claim_matched_with_differences():
    difference = ReconciliationDifference("cash", "account", "1", "2", "cash mismatch")
    with pytest.raises(PortError):
        ReconciliationReport(uuid4(), uuid4(), uuid4(), datetime.now(UTC), "matched", (difference,))


def test_reconciliation_tolerances_are_dimension_specific():
    access, account_id, local, broker_snapshot, now = _snapshots(mismatch=True)
    report = reconcile_account(
        local,
        broker_snapshot,
        tolerance=ReconciliationTolerance(Decimal("6000"), Decimal("3"), Decimal("0.2")),
        report_id=uuid4(),
        reconciled_at=now,
    )
    assert report.status == "mismatch"
    assert {item.category for item in report.differences} == {"fill"}
