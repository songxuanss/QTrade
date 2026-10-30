"""Email, webhook and in-app notification transport adapters."""

import hashlib
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from qtrade_domain.ports import (
    DeliveryReceipt,
    ErrorCode,
    Notification,
    NotificationInboxPort,
    NotificationPort,
    PortError,
)


class _SmtpClient(Protocol):
    def starttls(self, *, context: ssl.SSLContext) -> object: ...

    def login(self, user: str, password: str) -> object: ...

    def send_message(self, msg: EmailMessage) -> object: ...

    def quit(self) -> object: ...


@dataclass(frozen=True, slots=True)
class SmtpConfiguration:
    host: str
    sender: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    timeout_seconds: float = 10

    def __post_init__(self) -> None:
        if (
            not self.host
            or not 1 <= self.port <= 65535
            or not self.sender
            or self.timeout_seconds <= 0
            or (self.username is None) != (self.password is None)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid email transport configuration")


class SmtpEmailAdapter:
    def __init__(
        self,
        configuration: SmtpConfiguration | None,
        *,
        client_factory: Callable[..., _SmtpClient] = smtplib.SMTP,
    ):
        self._configuration = configuration
        self._client_factory = client_factory

    def send(self, notification: Notification) -> DeliveryReceipt:
        if notification.channel != "email":
            raise PortError(ErrorCode.UNSUPPORTED, "email adapter requires email channel")
        config = self._configuration
        if config is None:
            raise PortError(ErrorCode.UNSUPPORTED, "email delivery is not configured")
        _, address = parseaddr(notification.destination_id)
        if (
            not address
            or address != notification.destination_id
            or any(char in notification.subject + notification.destination_id for char in "\r\n")
            or "@" not in address
            or any(char.isspace() for char in address)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid email recipient")
        message = EmailMessage()
        message["From"] = config.sender
        message["To"] = address
        message["Subject"] = notification.subject
        message["Message-ID"] = f"<{notification.notification_id}@qtrade.local>"
        message.set_content(notification.body)
        client: _SmtpClient | None = None
        try:
            client = self._client_factory(config.host, config.port, timeout=config.timeout_seconds)
            client.starttls(context=ssl.create_default_context())
            if config.username is not None and config.password is not None:
                client.login(config.username, config.password)
            refused = client.send_message(message)
            if isinstance(refused, dict) and refused:
                raise PortError(ErrorCode.REJECTED, "email server rejected recipient")
            return DeliveryReceipt(notification.notification_id, message["Message-ID"], "accepted")
        except (TimeoutError, OSError, smtplib.SMTPException):
            raise PortError(
                ErrorCode.UNAVAILABLE, "email transport failed", retry_after=30
            ) from None
        finally:
            if client is not None:
                try:
                    client.quit()
                except (OSError, smtplib.SMTPException):
                    pass


class WebhookAdapter:
    def __init__(
        self,
        destinations: dict[str, str],
        *,
        allowed_hosts: frozenset[str],
        client: httpx.Client | None = None,
        allow_local_http: bool = False,
        timeout_seconds: float = 10,
    ):
        self._destinations = dict(destinations)
        self._allowed_hosts = frozenset(host.casefold() for host in allowed_hosts)
        self._client = client or httpx.Client(timeout=timeout_seconds, trust_env=False)
        self._allow_local_http = allow_local_http

    def send(self, notification: Notification) -> DeliveryReceipt:
        if notification.channel != "webhook":
            raise PortError(ErrorCode.UNSUPPORTED, "webhook adapter requires webhook channel")
        target = self._destinations.get(notification.destination_id)
        if target is None:
            raise PortError(ErrorCode.UNSUPPORTED, "webhook destination is not configured")
        parts = urlsplit(target)
        local_http = (
            self._allow_local_http
            and parts.scheme == "http"
            and parts.hostname
            in {
                "localhost",
                "127.0.0.1",
            }
        )
        if (
            not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.scheme != "https"
            and not local_http
            or parts.hostname.casefold() not in self._allowed_hosts
            and not local_http
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "webhook destination is not allowed")
        try:
            response = self._client.post(
                target,
                json={
                    "notification_id": str(notification.notification_id),
                    "workspace_id": str(notification.workspace_id),
                    "subject": notification.subject,
                    "body": notification.body,
                },
                headers={"Idempotency-Key": str(notification.notification_id)},
            )
        except httpx.TimeoutException:
            raise PortError(
                ErrorCode.TIMEOUT, "webhook request timed out", retry_after=30
            ) from None
        except httpx.RequestError:
            raise PortError(
                ErrorCode.UNAVAILABLE, "webhook request failed", retry_after=30
            ) from None
        if response.status_code == 429:
            raise PortError(ErrorCode.RATE_LIMITED, "webhook rate limited", retry_after=30)
        if response.status_code >= 500:
            raise PortError(ErrorCode.UNAVAILABLE, "webhook server failed", retry_after=30)
        if not 200 <= response.status_code < 300:
            raise PortError(ErrorCode.REJECTED, "webhook rejected notification")
        reference = hashlib.sha256(
            f"{notification.notification_id}:{response.status_code}".encode()
        ).hexdigest()[:32]
        return DeliveryReceipt(notification.notification_id, reference, "accepted")


class InAppNotificationAdapter:
    def __init__(self, inbox: NotificationInboxPort):
        self._inbox = inbox

    def send(self, notification: Notification) -> DeliveryReceipt:
        if notification.channel != "in_app":
            raise PortError(ErrorCode.UNSUPPORTED, "in-app adapter requires in-app channel")
        return self._inbox.record(notification, datetime.now(UTC))


class NotificationDispatcher:
    def __init__(self, adapters: dict[str, NotificationPort]):
        self._adapters = dict(adapters)

    def send(self, notification: Notification) -> DeliveryReceipt:
        adapter = self._adapters.get(notification.channel)
        if adapter is None:
            raise PortError(ErrorCode.UNSUPPORTED, "notification channel is not configured")
        return adapter.send(notification)
