"""Session composition from server-only configuration; no implicit encryption keys."""

import base64
import json
from collections.abc import Mapping
from datetime import timedelta

from qtrade_application.sessions import SessionService
from qtrade_crypto.tokens import TokenCipher
from qtrade_postgres.sessions import PostgresSessionStore
from qtrade_runtime.cookies import SessionCookie


def configure_sessions(
    environment: Mapping[str, str],
) -> tuple[SessionService | None, SessionCookie]:
    mode = environment.get("QTRADE_ENV", "local")
    insecure = environment.get("QTRADE_INSECURE_LOCAL_COOKIES", "false")
    if insecure not in {"true", "false"}:
        raise ValueError("invalid local cookie configuration")
    policy = SessionCookie(mode, insecure_local=insecure == "true")
    encoded = environment.get("QTRADE_SESSION_KEYS_JSON")
    if not encoded:
        if mode in {"production", "staging"}:
            raise ValueError("session encryption configuration is required")
        return None, policy
    try:
        decoded = json.loads(encoded)
        if (
            not isinstance(decoded, dict)
            or not decoded
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in decoded.items()
            )
        ):
            raise ValueError("invalid keyring")
        keys = {key: base64.b64decode(value, validate=True) for key, value in decoded.items()}
        primary = environment["QTRADE_SESSION_PRIMARY_KEY"]
        url = environment["DATABASE_URL"]
        if not url:
            raise ValueError("database configuration missing")
        lifetime = timedelta(
            seconds=int(environment.get("QTRADE_SESSION_LIFETIME_SECONDS", "28800"))
        )
    except (ValueError, KeyError, TypeError):
        raise ValueError("invalid session server configuration") from None
    service = SessionService(
        PostgresSessionStore(url), TokenCipher(keys, primary=primary), lifetime=lifetime
    )
    return service, policy
