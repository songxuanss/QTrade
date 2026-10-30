"""Workspace-scoped in-app notification persistence."""

from datetime import datetime
from typing import cast
from uuid import UUID

from qtrade_domain.ports import (
    DeliveryReceipt,
    ErrorCode,
    Notification,
    NotificationInboxPort,
    PortError,
)
from qtrade_postgres.connection import PostgresStore


class PostgresNotificationInbox(PostgresStore, NotificationInboxPort):
    def record(self, notification: Notification, now: datetime) -> DeliveryReceipt:
        if notification.channel != "in_app" or now.tzinfo is None:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid in-app notification")
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO in_app_notification "
                "(workspace_id,notification_id,destination_id,subject,body,created_at) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    notification.workspace_id,
                    notification.notification_id,
                    notification.destination_id,
                    notification.subject,
                    notification.body,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT destination_id,subject,body FROM in_app_notification "
                "WHERE workspace_id=%s AND notification_id=%s",
                (notification.workspace_id, notification.notification_id),
            ).fetchone()
            if row != (
                notification.destination_id,
                notification.subject,
                notification.body,
            ):
                raise PortError(ErrorCode.CONFLICT, "notification ID reused")
        return DeliveryReceipt(
            notification.notification_id, str(notification.notification_id), "delivered"
        )

    def list(
        self, workspace_id: UUID, destination_id: str, limit: int = 100
    ) -> tuple[Notification, ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid notification inbox limit")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT workspace_id,notification_id,destination_id,subject,body "
                "FROM in_app_notification WHERE workspace_id=%s AND destination_id=%s "
                "ORDER BY created_at DESC LIMIT %s",
                (workspace_id, destination_id, limit),
            ).fetchall()
        return tuple(
            Notification(
                cast(UUID, row[0]),
                cast(UUID, row[1]),
                "in_app",
                cast(str, row[2]),
                cast(str, row[3]),
                cast(str, row[4]),
            )
            for row in rows
        )
