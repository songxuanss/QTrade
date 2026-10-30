"""Consume public collector incidents into QTrade's existing alert/notification ports."""

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import uuid5

from qtrade_application.alerts import AlertEventFacts, AlertEventService
from qtrade_data_contracts.freshness_delivery import FreshnessNoticeLease
from qtrade_data_core.ports import DataFailure
from qtrade_data_http.client import CollectorClient
from qtrade_domain.alerts import AlertSeverity
from qtrade_domain.authorization import Permission
from qtrade_domain.ports import Notification
from qtrade_postgres.alert_events import PostgresAlertEventStore
from qtrade_postgres.notification_delivery import PostgresNotificationDeliveryQueue
from qtrade_postgres.workspaces import PostgresWorkspaceStore

logger = logging.getLogger(__name__)


class CollectorFreshnessConsumer:
    def __init__(self, client: CollectorClient, env: Mapping[str, str]):
        self.client = client
        self.consumer = env.get("COLLECTOR_FRESHNESS_CONSUMER_ID", "qtrade-alerts")
        self.workspaces = PostgresWorkspaceStore(env["DATABASE_URL"])
        self.events = AlertEventService(PostgresAlertEventStore(env["DATABASE_URL"]))
        self.notifications = PostgresNotificationDeliveryQueue(env["DATABASE_URL"])

    def consume(self) -> int:
        values = self.client.request(
            "POST", "/v1/freshness/notices/claim", body={"consumer_id": self.consumer, "limit": 20}
        )
        count = 0
        for value in values:
            lease = None
            try:
                lease = FreshnessNoticeLease.model_validate_json(json.dumps(value))
                if lease.consumer_id != self.consumer or lease.expires_at <= datetime.now(UTC):
                    continue
                if self.consume_one(lease):
                    count += 1
            except Exception as error:
                # Persisted leases will expire and be reconsidered. Never log
                # remote exception text or credentials from an upstream failure.
                code = getattr(error, "code", "unavailable")
                if not isinstance(code, str) or code not in {
                    "forbidden",
                    "rejected",
                    "conflict",
                    "timeout",
                    "unavailable",
                }:
                    code = "unavailable"
                logger.warning(
                    "collector freshness delivery deferred: scope=%s notice=%s code=%s",
                    str(lease.scope_id) if lease else "unverified",
                    str(lease.notice.notice_id) if lease else "unverified",
                    code,
                )
        return count

    def consume_one(self, lease: FreshnessNoticeLease) -> bool:
        """Acknowledge durable acceptance, not final channel delivery.

        Event and notification IDs are stable on retry. A crash between either
        local commit and the remote ack cannot lose the alert or duplicate its
        notification. The existing channel worker owns final delivery/retries.
        """
        try:
            lease = FreshnessNoticeLease.model_validate_json(lease.model_dump_json())
        except ValueError:
            raise DataFailure("unverified") from None
        notice, key = lease.notice, lease.notice.result.key
        if lease.consumer_id != self.consumer or lease.expires_at <= datetime.now(UTC):
            return False
        access = self.workspaces.access(notice.actor_id, notice.scope_id)
        if access is None:
            return False
        access.require(Permission.DATA_MANAGE)
        recovered = notice.kind == "recovered"
        title = ("数据新鲜度恢复" if recovered else "数据新鲜度异常") + f" · {key.instrument_id}"
        expected = notice.result.expectation
        published = notice.result.evidence.published
        message = (
            f"来源 {key.source}，{key.instrument_id}，{key.period or key.dataset_kind}。"
            f"预期截至 {expected.complete_through if expected else '规则未知'}，"
            f"已发布截至 {published.complete_through if published else '未验证'}。"
            + ("已覆盖预期周期且来源可用。" if recovered else "请核对来源、采集和发布状态。")
        )
        event = self.events.data(
            AlertEventFacts(
                workspace_id=notice.scope_id,
                source_event_id=f"collector-freshness:{notice.notice_id}",
                event_type=f"collection.freshness.{notice.kind}",
                severity=AlertSeverity.INFO if recovered else AlertSeverity.WARNING,
                title=title,
                message=message,
                occurred_at=notice.occurred_at,
                resource_id=f"freshness:{notice.episode_id}",
                context_snapshot_json=json.dumps(
                    {
                        "collection_freshness": {
                            "notice_id": str(notice.notice_id),
                            "episode_id": str(notice.episode_id),
                            "kind": notice.kind,
                            "source": key.source,
                            "instrument_id": key.instrument_id,
                            "mapping_version": key.mapping_version,
                            "status": notice.result.status,
                            "stage": notice.result.stage,
                            "reason": notice.result.reason,
                        }
                    },
                    sort_keys=True,
                ),
            )
        )
        self.notifications.enqueue(
            Notification(
                notice.scope_id,
                uuid5(event.event_id, f"in_app:{notice.scope_id}"),
                "in_app",
                str(notice.scope_id),
                event.title,
                event.message,
            ),
            datetime.now(UTC),
        )
        ack = self.client.request(
            "POST",
            f"/v1/freshness/notices/{notice.notice_id}/ack",
            actor_id=notice.actor_id,
            scope_id=notice.scope_id,
            body={"consumer_id": self.consumer, "token": str(lease.token)},
        )
        if ack != {"accepted": True}:
            raise DataFailure("unavailable")
        return True
