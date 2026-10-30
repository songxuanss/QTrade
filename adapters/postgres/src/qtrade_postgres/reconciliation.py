"""Append-only reconciliation reports and fail-closed account gates."""

import json
from datetime import datetime
from typing import cast
from uuid import UUID

from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.reconciliation import (
    ReconciliationCategory,
    ReconciliationDifference,
    ReconciliationReport,
    ReconciliationStatus,
)
from qtrade_postgres.connection import PostgresStore


class PostgresReconciliationReportStore(PostgresStore):
    def record(self, report: ReconciliationReport) -> ReconciliationReport:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO reconciliation_report(workspace_id,report_id,account_id,"
                "reconciled_at,status,payload) VALUES (%s,%s,%s,%s,%s,%s::jsonb) "
                "ON CONFLICT (workspace_id,report_id) DO NOTHING",
                (
                    report.workspace_id,
                    report.report_id,
                    report.account_id,
                    report.reconciled_at,
                    report.status,
                    _encode(report),
                ),
            )
            row = conn.execute(
                "SELECT payload FROM reconciliation_report WHERE workspace_id=%s AND report_id=%s",
                (report.workspace_id, report.report_id),
            ).fetchone()
        if row is None:
            raise PortError(ErrorCode.UNAVAILABLE, "reconciliation report persistence unavailable")
        stored = _decode(row[0])
        if stored != report:
            raise PortError(ErrorCode.CONFLICT, "reconciliation report identity reused")
        return stored

    def latest(self, workspace_id: UUID, account_id: UUID) -> ReconciliationReport | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM reconciliation_report WHERE workspace_id=%s AND account_id=%s "
                "ORDER BY reconciled_at DESC,report_id DESC LIMIT 1",
                (workspace_id, account_id),
            ).fetchone()
        return _decode(row[0]) if row else None


class PostgresAccountTradingGateStore(PostgresStore):
    def block(
        self,
        workspace_id: UUID,
        account_id: UUID,
        report_id: UUID,
        occurred_at: datetime,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO live_account_gate(workspace_id,account_id,report_id,blocked_at) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (workspace_id,account_id) DO UPDATE SET "
                "report_id=EXCLUDED.report_id,blocked_at=EXCLUDED.blocked_at",
                (workspace_id, account_id, report_id, occurred_at),
            )

    def is_blocked(self, workspace_id: UUID, account_id: UUID) -> bool:
        with self._connection() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM live_account_gate WHERE workspace_id=%s AND account_id=%s",
                    (workspace_id, account_id),
                ).fetchone()
                is not None
            )


def _encode(report: ReconciliationReport) -> str:
    return json.dumps(
        {
            "report_id": str(report.report_id),
            "workspace_id": str(report.workspace_id),
            "account_id": str(report.account_id),
            "reconciled_at": report.reconciled_at.isoformat(),
            "status": report.status,
            "differences": [
                {
                    "category": item.category,
                    "resource_id": item.resource_id,
                    "local_value": item.local_value,
                    "broker_value": item.broker_value,
                    "summary": item.summary,
                }
                for item in report.differences
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode(value: object) -> ReconciliationReport:
    try:
        payload = cast(dict[str, object], value)
        differences = cast(list[dict[str, str]], payload["differences"])
        return ReconciliationReport(
            UUID(str(payload["report_id"])),
            UUID(str(payload["workspace_id"])),
            UUID(str(payload["account_id"])),
            datetime.fromisoformat(str(payload["reconciled_at"]).replace("Z", "+00:00")),
            cast(ReconciliationStatus, str(payload["status"])),
            tuple(
                ReconciliationDifference(
                    cast(ReconciliationCategory, item["category"]),
                    item["resource_id"],
                    item["local_value"],
                    item["broker_value"],
                    item["summary"],
                )
                for item in differences
            ),
        )
    except (KeyError, TypeError, ValueError, PortError):
        raise PortError(ErrorCode.UNAVAILABLE, "invalid stored reconciliation report") from None
