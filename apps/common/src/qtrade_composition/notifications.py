"""Environment-backed notification queue and channel composition."""

import json
import os
from collections.abc import Mapping

from qtrade_domain.notification_delivery import NotificationDeliveryQueuePort
from qtrade_domain.ports import ErrorCode, NotificationPort, PortError
from qtrade_notifications.dispatch import (
    InAppNotificationAdapter,
    NotificationDispatcher,
    SmtpConfiguration,
    SmtpEmailAdapter,
    WebhookAdapter,
)
from qtrade_postgres.notification_delivery import PostgresNotificationDeliveryQueue
from qtrade_postgres.notification_inbox import PostgresNotificationInbox


def notification_composition_from_env(
    env: Mapping[str, str] = os.environ,
) -> tuple[NotificationDeliveryQueuePort, NotificationPort]:
    database_url = env["DATABASE_URL"]
    queue = PostgresNotificationDeliveryQueue(database_url)
    smtp: SmtpConfiguration | None = None
    host, sender = env.get("SMTP_HOST"), env.get("SMTP_SENDER")
    if host and sender:
        try:
            smtp = SmtpConfiguration(
                host,
                sender,
                int(env.get("SMTP_PORT", "587")),
                env.get("SMTP_USERNAME"),
                env.get("SMTP_PASSWORD"),
            )
        except (ValueError, PortError):
            raise PortError(
                ErrorCode.INVALID_INPUT, "invalid email transport configuration"
            ) from None
    destinations: dict[str, str] = {}
    configured = env.get("QTRADE_WEBHOOK_DESTINATIONS", "{}")
    try:
        raw = json.loads(configured)
        if not isinstance(raw, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in raw.items()
        ):
            raise ValueError
        destinations = raw
    except (ValueError, TypeError):
        raise PortError(
            ErrorCode.INVALID_INPUT, "invalid webhook destination configuration"
        ) from None
    allowed_hosts = frozenset(
        host.strip()
        for host in env.get("QTRADE_WEBHOOK_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    )
    dispatcher = NotificationDispatcher(
        {
            "in_app": InAppNotificationAdapter(PostgresNotificationInbox(database_url)),
            "email": SmtpEmailAdapter(smtp),
            "webhook": WebhookAdapter(
                destinations,
                allowed_hosts=allowed_hosts,
                allow_local_http=env.get("QTRADE_ENV") == "local",
            ),
        }
    )
    return queue, dispatcher
