"""Durable notification outbox, fenced leases and immutable attempt history."""

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from qtrade_domain.notification_delivery import (
    NotificationAttempt,
    NotificationAttemptStatus,
    NotificationDeliveryLease,
    NotificationDeliveryQueuePort,
)
from qtrade_domain.ports import ErrorCode, Notification, NotificationChannel, PortError
from qtrade_postgres.connection import PostgresStore


class PostgresNotificationDeliveryQueue(PostgresStore, NotificationDeliveryQueuePort):
    def enqueue(self, notification: Notification, now: datetime) -> None:
        if now.tzinfo is None:
            raise PortError(ErrorCode.INVALID_INPUT, "notification timestamp must be aware")
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO notification_delivery "
                "(workspace_id,notification_id,channel,destination_id,subject,body,status,"
                "available_at,created_at,updated_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    notification.workspace_id,
                    notification.notification_id,
                    notification.channel,
                    notification.destination_id,
                    notification.subject,
                    notification.body,
                    now,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT channel,destination_id,subject,body FROM notification_delivery "
                "WHERE workspace_id=%s AND notification_id=%s",
                (notification.workspace_id, notification.notification_id),
            ).fetchone()
            if row != (
                notification.channel,
                notification.destination_id,
                notification.subject,
                notification.body,
            ):
                raise PortError(ErrorCode.CONFLICT, "notification ID reused")

    def ready(
        self, now: datetime, limit: int, lease_seconds: int
    ) -> tuple[NotificationDeliveryLease, ...]:
        if (
            now.tzinfo is None
            or type(limit) is not int
            or not 1 <= limit <= 100
            or type(lease_seconds) is not int
            or not 5 <= lease_seconds <= 300
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid notification queue poll")
        leases: list[NotificationDeliveryLease] = []
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT workspace_id,notification_id,channel,destination_id,subject,body,attempts "
                "FROM notification_delivery WHERE "
                "((status='queued' AND available_at<=%s) OR "
                "(status='running' AND lease_expires_at<=%s)) "
                "ORDER BY available_at,created_at LIMIT %s FOR UPDATE SKIP LOCKED",
                (now, now, limit),
            ).fetchall()
            for row in rows:
                workspace_id, notification_id = cast(UUID, row[0]), cast(UUID, row[1])
                notification = Notification(
                    workspace_id,
                    notification_id,
                    cast(NotificationChannel, row[2]),  # validated by Notification
                    cast(str, row[3]),
                    cast(str, row[4]),
                    cast(str, row[5]),
                )
                attempt = cast(int, row[6]) + 1
                token = uuid4()
                conn.execute(
                    "UPDATE notification_delivery SET status='running',attempts=%s,"
                    "lease_token=%s,lease_expires_at=%s,updated_at=%s "
                    "WHERE workspace_id=%s AND notification_id=%s",
                    (
                        attempt,
                        token,
                        now + timedelta(seconds=lease_seconds),
                        now,
                        workspace_id,
                        notification_id,
                    ),
                )
                leases.append(NotificationDeliveryLease(notification, token, attempt))
        return tuple(leases)

    def finish(
        self,
        lease: NotificationDeliveryLease,
        attempt: NotificationAttempt,
        *,
        retry_after_seconds: int | None,
        max_attempts: int = 8,
    ) -> bool:
        if (
            attempt.workspace_id != lease.notification.workspace_id
            or attempt.notification_id != lease.notification.notification_id
            or attempt.attempt != lease.attempt
            or type(max_attempts) is not int
            or not 1 <= max_attempts <= 20
            or (
                retry_after_seconds is not None
                and (type(retry_after_seconds) is not int or retry_after_seconds < 1)
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid notification completion")
        if attempt.status == "delivered":
            next_status, delay = "delivered", 0
        elif retry_after_seconds is not None and lease.attempt < max_attempts:
            next_status, delay = "queued", retry_after_seconds
        else:
            next_status, delay = "failed", 0
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM notification_delivery WHERE workspace_id=%s AND notification_id=%s "
                "AND status='running' AND lease_token=%s FOR UPDATE",
                (
                    lease.notification.workspace_id,
                    lease.notification.notification_id,
                    lease.token,
                ),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "INSERT INTO notification_attempt "
                "(workspace_id,notification_id,attempt,channel,target,status,response_summary,"
                "attempted_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    attempt.workspace_id,
                    attempt.notification_id,
                    attempt.attempt,
                    attempt.channel,
                    attempt.target,
                    attempt.status,
                    attempt.response_summary,
                    attempt.attempted_at,
                ),
            )
            conn.execute(
                "UPDATE notification_delivery SET status=%s,available_at=%s,lease_token=NULL,"
                "lease_expires_at=NULL,updated_at=%s WHERE workspace_id=%s AND notification_id=%s",
                (
                    next_status,
                    attempt.attempted_at + timedelta(seconds=delay),
                    attempt.attempted_at,
                    attempt.workspace_id,
                    attempt.notification_id,
                ),
            )
        return True

    def attempts(
        self, workspace_id: UUID, notification_id: UUID
    ) -> tuple[NotificationAttempt, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT workspace_id,notification_id,channel,target,status,response_summary,"
                "attempted_at,attempt FROM notification_attempt WHERE workspace_id=%s "
                "AND notification_id=%s ORDER BY attempt",
                (workspace_id, notification_id),
            ).fetchall()
        return tuple(
            NotificationAttempt(
                cast(UUID, row[0]),
                cast(UUID, row[1]),
                cast(NotificationChannel, row[2]),
                cast(str, row[3]),
                cast(NotificationAttemptStatus, row[4]),
                cast(str, row[5]),
                cast(datetime, row[6]),
                cast(int, row[7]),
            )
            for row in rows
        )
