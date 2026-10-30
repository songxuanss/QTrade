"""Transport adapters never expose provider responses or credentials."""

import smtplib
from uuid import uuid4

import httpx
import pytest

from qtrade_domain.ports import ErrorCode, Notification, NotificationChannel, PortError
from qtrade_mock.ports import MockNotification
from qtrade_notifications.dispatch import (
    NotificationDispatcher,
    SmtpConfiguration,
    SmtpEmailAdapter,
    WebhookAdapter,
)


def _notification(
    channel: NotificationChannel = "webhook", destination: str = "ops-hook"
) -> Notification:
    return Notification(uuid4(), uuid4(), channel, destination, "Risk alert", "Limit exceeded")


def test_in_app_dispatch_is_idempotent_and_missing_channel_is_safe():
    port = MockNotification()
    dispatcher = NotificationDispatcher({"in_app": port})
    notification = _notification("in_app", "member-1")
    first = dispatcher.send(notification)
    assert dispatcher.send(notification) == first
    with pytest.raises(PortError) as error:
        dispatcher.send(_notification())
    assert error.value.code == ErrorCode.UNSUPPORTED


def test_email_missing_configuration_and_smtp_credentials_are_redacted():
    email = _notification("email", "trader@example.com")
    with pytest.raises(PortError, match="not configured"):
        SmtpEmailAdapter(None).send(email)

    class FailingSmtp:
        def starttls(self, *, context):
            return None

        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"password=private-token")

        def send_message(self, msg):
            return None

        def quit(self):
            return None

    adapter = SmtpEmailAdapter(
        SmtpConfiguration(
            "smtp.example.com",
            "alerts@example.com",
            587,
            "smtp-user",
            "private-token",
        ),
        client_factory=lambda *_args, **_kwargs: FailingSmtp(),
    )
    with pytest.raises(PortError) as error:
        adapter.send(email)
    assert error.value.code == ErrorCode.UNAVAILABLE
    assert "private-token" not in str(error.value)


def test_email_success_uses_tls_and_stable_message_identity():
    email = _notification("email", "trader@example.com")

    class RecordingSmtp:
        message = None

        def starttls(self, *, context):
            return None

        def login(self, user, password):
            return None

        def send_message(self, msg):
            self.message = msg
            return {}

        def quit(self):
            return None

    smtp = RecordingSmtp()
    adapter = SmtpEmailAdapter(
        SmtpConfiguration("smtp.example.com", "alerts@example.com"),
        client_factory=lambda *_args, **_kwargs: smtp,
    )
    receipt = adapter.send(email)
    assert receipt.notification_id == email.notification_id
    assert receipt.status == "accepted"
    assert smtp.message["To"] == "trader@example.com"
    assert smtp.message["Message-ID"] == f"<{email.notification_id}@qtrade.local>"


def test_webhook_success_timeout_non_2xx_and_secret_filtering():
    seen: list[httpx.Request] = []

    def successful(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(successful))
    adapter = WebhookAdapter(
        {"ops-hook": "https://hooks.example.com/notify?token=private-token"},
        allowed_hosts=frozenset({"hooks.example.com"}),
        client=client,
    )
    notification = _notification()
    receipt = adapter.send(notification)
    assert receipt.notification_id == notification.notification_id
    assert receipt.status == "accepted"
    assert b"private-token" not in receipt.provider_reference.encode()
    assert seen[0].headers["Idempotency-Key"] == str(notification.notification_id)
    assert b"private-token" not in seen[0].content

    failing = WebhookAdapter(
        {"ops-hook": "https://hooks.example.com/notify"},
        allowed_hosts=frozenset({"hooks.example.com"}),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(503, text="private-token leaked by server")
            )
        ),
    )
    with pytest.raises(PortError) as error:
        failing.send(notification)
    assert error.value.retryable
    assert "private-token" not in str(error.value)

    rejected = WebhookAdapter(
        {"ops-hook": "https://hooks.example.com/notify"},
        allowed_hosts=frozenset({"hooks.example.com"}),
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))),
    )
    with pytest.raises(PortError) as error:
        rejected.send(notification)
    assert error.value.code == ErrorCode.REJECTED
    assert not error.value.retryable

    timeout = WebhookAdapter(
        {"ops-hook": "https://hooks.example.com/notify"},
        allowed_hosts=frozenset({"hooks.example.com"}),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: (_ for _ in ()).throw(httpx.ReadTimeout("private-token"))
            )
        ),
    )
    with pytest.raises(PortError) as error:
        timeout.send(notification)
    assert error.value.code == ErrorCode.TIMEOUT
    assert "private-token" not in str(error.value)


def test_webhook_destination_must_be_configured_and_host_allowlisted():
    adapter = WebhookAdapter(
        {"internal": "http://169.254.169.254/latest/meta-data"},
        allowed_hosts=frozenset(),
    )
    with pytest.raises(PortError) as error:
        adapter.send(_notification(destination="internal"))
    assert error.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(PortError) as missing:
        adapter.send(_notification(destination="missing"))
    assert missing.value.code == ErrorCode.UNSUPPORTED
