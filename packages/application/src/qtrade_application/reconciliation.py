"""Account reconciliation workflow and fail-closed live order gate."""

import json
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from qtrade_application.alerts import AlertEventFacts, AlertEventService
from qtrade_application.kill_switch import KillSwitchService, KillSwitchTrigger
from qtrade_domain.alerts import AlertSeverity
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ports import BrokerPort, ErrorCode, PortError
from qtrade_domain.reconciliation import (
    AccountTradingGateStore,
    LocalAccountSnapshot,
    ReconciliationReport,
    ReconciliationReportStore,
    ReconciliationTolerance,
    reconcile_account,
)


class LocalAccountSnapshotPort(Protocol):
    def snapshot(self, workspace_id: UUID, account_id: UUID) -> LocalAccountSnapshot: ...


class ReconciliationService:
    def __init__(
        self,
        broker: BrokerPort,
        local: LocalAccountSnapshotPort,
        reports: ReconciliationReportStore,
        gates: AccountTradingGateStore,
        alerts: AlertEventService,
        kill_switches: KillSwitchService | None = None,
    ):
        self._broker, self._local = broker, local
        self._reports, self._gates, self._alerts = reports, gates, alerts
        self._kill_switches = kill_switches

    def reconcile(
        self,
        access: WorkspaceAccess,
        account_id: UUID,
        *,
        tolerance: ReconciliationTolerance,
        now: datetime | None = None,
    ) -> ReconciliationReport:
        access.require(Permission.RISK_CONFIGURE)
        workspace_id = access.workspace.workspace_id
        local = self._local.snapshot(workspace_id, account_id)
        broker = self._broker.snapshot(workspace_id, account_id)
        if local.workspace_id != workspace_id or local.account_id != account_id:
            raise PortError(ErrorCode.NOT_FOUND, "local account snapshot unavailable")
        if broker.workspace_id != workspace_id or broker.account_id != account_id:
            raise PortError(ErrorCode.UNAVAILABLE, "broker account snapshot scope mismatch")
        reconciled_at = now or datetime.now(UTC)
        report = reconcile_account(
            local,
            broker,
            tolerance=tolerance,
            report_id=uuid4(),
            reconciled_at=reconciled_at,
        )
        report = self._reports.record(report)
        if report.blocks_live_orders:
            self._gates.block(workspace_id, account_id, report.report_id, report.reconciled_at)
            if self._kill_switches is not None:
                self._kill_switches.trip_automatically(
                    workspace_id=workspace_id,
                    account_id=account_id,
                    trigger=KillSwitchTrigger.RECONCILIATION_MISMATCH,
                    reason=f"reconciliation report {report.report_id} has a mismatch",
                    now=report.reconciled_at,
                )
            self._alerts.risk(
                AlertEventFacts(
                    workspace_id=workspace_id,
                    source_event_id=str(report.report_id),
                    event_type="reconciliation.mismatch",
                    severity=AlertSeverity.CRITICAL,
                    title="Account reconciliation mismatch",
                    message=(
                        f"{len(report.differences)} broker/local difference(s) found; "
                        "live orders are blocked."
                    ),
                    occurred_at=report.reconciled_at,
                    resource_id=str(account_id),
                    context_snapshot_json=json.dumps(
                        {
                            "report_id": str(report.report_id),
                            "differences": [
                                {
                                    "category": difference.category,
                                    "resource_id": difference.resource_id,
                                    "local_value": difference.local_value,
                                    "broker_value": difference.broker_value,
                                    "summary": difference.summary,
                                }
                                for difference in report.differences
                            ],
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
        return report
