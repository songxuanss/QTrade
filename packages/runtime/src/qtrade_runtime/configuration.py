"""Fail-closed process configuration; errors name settings, never their values."""

import base64
import json
import os
import stat
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from urllib.parse import urlsplit

PROTECTED = {"staging", "production"}
BOOLEAN_SETTINGS = (
    "QTRADE_OIDC_ALLOW_HTTP",
    "QTRADE_INSECURE_LOCAL_COOKIES",
    "QTRADE_LIVE_TRADING_ENABLED",
    "QTRADE_MAIRUI_PRO_ENABLED",
)
SECRET_SETTINGS = frozenset(
    {
        "DATABASE_URL",
        "MARKET_DATABASE_URL",
        "REDIS_URL",
        "OBJECT_STORE_ACCESS_KEY",
        "OBJECT_STORE_SECRET_KEY",
        "MAIRUI_LICENCE",
        "QTRADE_OIDC_CLIENT_SECRET",
        "QTRADE_BFF_SECRET",
        "QTRADE_OIDC_TRANSACTION_KEY",
        "QTRADE_SESSION_KEYS_JSON",
        "QTRADE_SESSION_PRIMARY_KEY",
        "SMTP_PASSWORD",
        "QTRADE_WEBHOOK_DESTINATIONS",
    }
)


class ConfigurationError(ValueError):
    """A safe, actionable configuration error with no private values."""


def load_secrets(environment: MutableMapping[str, str]) -> None:
    filename = environment.get("QTRADE_SECRETS_FILE")
    if not filename:
        if environment.get("QTRADE_ENV") in PROTECTED:
            raise ConfigurationError("QTRADE_SECRETS_FILE is required in staging/production")
        return
    try:
        fd = os.open(Path(filename), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r") as file:
            info = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_uid not in {0, os.geteuid()}
                or info.st_size > 65_536
            ):
                raise ValueError
            values = json.load(file)
        if (
            not isinstance(values, dict)
            or not values
            or any(
                key not in SECRET_SETTINGS or not isinstance(value, str) or not value
                for key, value in values.items()
            )
        ):
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise ConfigurationError("QTRADE_SECRETS_FILE must be a private, valid JSON file") from None
    environment.update(values)


def validate_configuration(environment: Mapping[str, str], *, isolated: bool = False) -> None:
    mode = environment.get("QTRADE_ENV", "local")
    if mode not in {"local", "dev", "test", "staging", "production"}:
        raise ConfigurationError("QTRADE_ENV must name a supported environment")
    for name in BOOLEAN_SETTINGS:
        if environment.get(name, "false") not in {"true", "false"}:
            raise ConfigurationError(f"{name} must be true or false")
    if environment.get("QTRADE_LIVE_TRADING_ENABLED", "false") == "true":
        # Current composition has no real broker. A flag must never manufacture readiness.
        raise ConfigurationError(
            "Live adapter is not configured; keep QTRADE_LIVE_TRADING_ENABLED=false"
        )
    if mode not in PROTECTED:
        return
    if any(environment.get(name) == "true" for name in BOOLEAN_SETTINGS[:2]):
        raise ConfigurationError("staging/production require HTTPS and secure cookies")
    if isolated:
        return
    for name in ("QTRADE_OIDC_ISSUER", "QTRADE_PUBLIC_ORIGIN", "QTRADE_API_PUBLIC_ORIGIN"):
        try:
            url = urlsplit(environment[name])
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or (name != "QTRADE_OIDC_ISSUER" and url.path not in {"", "/"})
            ):
                raise ValueError
        except (KeyError, ValueError):
            raise ConfigurationError(f"{name} must be a public HTTPS address") from None
    for name in ("QTRADE_OIDC_CLIENT_ID", "QTRADE_OIDC_CLIENT_SECRET", "QTRADE_CONFIG_VERSION"):
        if not environment.get(name):
            raise ConfigurationError(f"{name} is required")
    if len(environment.get("QTRADE_BFF_SECRET", "")) < 32:
        raise ConfigurationError("QTRADE_BFF_SECRET must contain at least 32 characters")
    try:
        transaction = base64.b64decode(environment["QTRADE_OIDC_TRANSACTION_KEY"], validate=True)
        keys = json.loads(environment["QTRADE_SESSION_KEYS_JSON"])
        if (
            len(transaction) != 32
            or not isinstance(keys, dict)
            or not keys
            or environment["QTRADE_SESSION_PRIMARY_KEY"] not in keys
            or any(
                not isinstance(key, str)
                or not key
                or not isinstance(value, str)
                or len(base64.b64decode(value, validate=True)) != 32
                for key, value in keys.items()
            )
        ):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise ConfigurationError(
            "session and transaction encryption key configuration is invalid"
        ) from None
    for name in ("DATABASE_URL", "MARKET_DATABASE_URL", "REDIS_URL", "OBJECT_STORE_URL"):
        if not environment.get(name):
            raise ConfigurationError(f"{name} is required")
    for name in SECRET_SETTINGS:
        value = environment.get(name, "")
        if "local-only" in value or value in {"qtrade-local", "changeme", "password"}:
            raise ConfigurationError(f"{name} contains a development default")
