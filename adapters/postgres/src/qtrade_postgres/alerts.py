"""Durable append-only alert rule revision storage."""

import json
from datetime import datetime
from typing import cast
from uuid import UUID

from qtrade_domain.alerts import AlertRuleVersion
from qtrade_domain.ports import ErrorCode, NotificationChannel, PortError
from qtrade_postgres.connection import PostgresStore
from qtrade_postgres.query import column_query


class PostgresAlertRuleStore(PostgresStore):
    _COLUMNS = (
        "workspace_id,rule_id,version,name,source,condition,severity,"
        "cooldown_seconds,enabled,created_at,notification_targets"
    )
    _INSERT = "INSERT INTO alert_rule_version ({}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)"

    @staticmethod
    def _decode(row: tuple[object, ...]) -> AlertRuleVersion:
        try:
            targets_raw = row[10]
            if not isinstance(targets_raw, list) or any(
                not isinstance(item, dict) for item in targets_raw
            ):
                raise ValueError("invalid notification targets")
            targets = tuple((item["channel"], item["destination_id"]) for item in targets_raw)
            return AlertRuleVersion(
                UUID(str(row[1])),
                UUID(str(row[0])),
                cast(int, row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
                cast(int, row[7]),
                bool(row[8]),
                cast(datetime, row[9]),
                cast(tuple[tuple[NotificationChannel, str], ...], targets),
            )
        except (KeyError, IndexError, TypeError, ValueError, PortError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid stored alert rule") from None

    def create(self, rule: AlertRuleVersion) -> AlertRuleVersion:
        with self._connection() as conn:
            conn.execute(
                "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE",
                (rule.workspace_id,),
            )
            conn.execute(
                self._INSERT.format(self._COLUMNS),
                (
                    rule.workspace_id,
                    rule.rule_id,
                    rule.version,
                    rule.name,
                    rule.source,
                    rule.condition,
                    rule.severity,
                    rule.cooldown_seconds,
                    rule.enabled,
                    rule.created_at,
                    json.dumps(
                        [
                            {"channel": channel, "destination_id": target}
                            for channel, target in rule.notification_targets
                        ],
                        separators=(",", ":"),
                    ),
                ),
            )
        return rule

    def current(self, workspace_id: UUID, rule_id: UUID) -> AlertRuleVersion | None:
        with self._connection() as conn:
            row = conn.execute(
                column_query(
                    "SELECT {} FROM alert_rule_version WHERE "
                    "workspace_id=%s AND rule_id=%s ORDER BY version DESC"
                    " LIMIT 1",
                    self._COLUMNS,
                ),
                (workspace_id, rule_id),
            ).fetchone()
        return self._decode(row) if row else None

    def append(self, rule: AlertRuleVersion) -> AlertRuleVersion:
        with self._connection() as conn:
            conn.execute(
                "SELECT workspace_id FROM workspace WHERE workspace_id=%s FOR UPDATE",
                (rule.workspace_id,),
            )
            latest = conn.execute(
                "SELECT version FROM alert_rule_version "
                "WHERE workspace_id=%s AND rule_id=%s "
                "ORDER BY version DESC LIMIT 1 FOR UPDATE",
                (rule.workspace_id, rule.rule_id),
            ).fetchone()
            if latest is None:
                raise PortError(ErrorCode.NOT_FOUND, "alert rule unavailable")
            if rule.version != cast(int, latest[0]) + 1:
                raise PortError(ErrorCode.CONFLICT, "alert rule version changed")
            conn.execute(
                self._INSERT.format(self._COLUMNS),
                (
                    rule.workspace_id,
                    rule.rule_id,
                    rule.version,
                    rule.name,
                    rule.source,
                    rule.condition,
                    rule.severity,
                    rule.cooldown_seconds,
                    rule.enabled,
                    rule.created_at,
                    json.dumps(
                        [
                            {"channel": channel, "destination_id": target}
                            for channel, target in rule.notification_targets
                        ],
                        separators=(",", ":"),
                    ),
                ),
            )
        return rule

    def list(self, workspace_id: UUID) -> tuple[AlertRuleVersion, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                column_query(
                    "SELECT DISTINCT ON (rule_id) {} FROM "
                    "alert_rule_version WHERE workspace_id=%s ORDER BY "
                    "rule_id,version DESC",
                    self._COLUMNS,
                ),
                (workspace_id,),
            ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def versions(self, workspace_id: UUID, rule_id: UUID) -> tuple[AlertRuleVersion, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                column_query(
                    "SELECT {} FROM alert_rule_version WHERE "
                    "workspace_id=%s AND rule_id=%s ORDER BY version",
                    self._COLUMNS,
                ),
                (workspace_id, rule_id),
            ).fetchall()
        return tuple(self._decode(row) for row in rows)
