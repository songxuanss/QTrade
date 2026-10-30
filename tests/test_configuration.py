"""Unsafe deployment configurations must fail before any process can serve traffic."""

import base64
import json
import os
from pathlib import Path

import pytest

from qtrade_runtime.configuration import ConfigurationError, load_secrets, validate_configuration


def protected_configuration() -> dict[str, str]:
    key = base64.b64encode(os.urandom(32)).decode()
    return {
        "QTRADE_ENV": "production",
        "QTRADE_OIDC_ISSUER": "https://identity.example.test",
        "QTRADE_PUBLIC_ORIGIN": "https://trade.example.test",
        "QTRADE_API_PUBLIC_ORIGIN": "https://api.example.test",
        "QTRADE_OIDC_CLIENT_ID": "qtrade",
        "QTRADE_OIDC_CLIENT_SECRET": "private-client-configuration",
        "QTRADE_CONFIG_VERSION": "config.v1",
        "QTRADE_BFF_SECRET": base64.b64encode(os.urandom(32)).decode(),
        "QTRADE_OIDC_TRANSACTION_KEY": key,
        "QTRADE_SESSION_KEYS_JSON": json.dumps({"v1": key}),
        "QTRADE_SESSION_PRIMARY_KEY": "v1",
        "DATABASE_URL": "postgresql://private:private@domain-db/qtrade",
        "MARKET_DATABASE_URL": "postgresql://private:private@market-db/qtrade_market",
        "REDIS_URL": "redis://cache:6379/0",
        "OBJECT_STORE_URL": "http://object-store:9000",
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("QTRADE_ENV", "prodction"),
        ("QTRADE_OIDC_ISSUER", "http://identity.example.test"),
        ("QTRADE_PUBLIC_ORIGIN", "https://private:private@trade.example.test"),
        ("QTRADE_API_PUBLIC_ORIGIN", "http://api.example.test"),
        ("QTRADE_BFF_SECRET", "short-private-value"),
        ("QTRADE_SESSION_PRIMARY_KEY", "missing-key"),
        ("QTRADE_SESSION_KEYS_JSON", '{"v1":"malformed-key"}'),
        ("QTRADE_OIDC_TRANSACTION_KEY", "malformed-key"),
        ("QTRADE_OIDC_ALLOW_HTTP", "true"),
        ("QTRADE_INSECURE_LOCAL_COOKIES", "true"),
        ("QTRADE_LIVE_TRADING_ENABLED", "true"),
        ("QTRADE_LIVE_TRADING_ENABLED", "yes"),
        ("DATABASE_URL", "postgresql://qtrade:qtrade-local-only@db/qtrade"),
    ],
)
def test_protected_configuration_rejects_unsafe_settings_without_values(name, value):
    env = protected_configuration()
    env[name] = value
    with pytest.raises(ConfigurationError) as error:
        validate_configuration(env)
    if "private" in value or "malformed-key" in value:
        assert value not in str(error.value)


def test_valid_protected_configuration_and_isolated_runner():
    validate_configuration(protected_configuration())
    validate_configuration({"QTRADE_ENV": "production"}, isolated=True)


def test_mounted_secrets_are_private_bounded_and_do_not_follow_links(tmp_path):
    path = tmp_path / "configuration.secrets.json"
    path.write_text(json.dumps({"QTRADE_BFF_SECRET": "private-mounted-value"}))
    path.chmod(0o600)
    env = {"QTRADE_ENV": "production", "QTRADE_SECRETS_FILE": str(path)}
    load_secrets(env)
    assert env["QTRADE_BFF_SECRET"] == "private-mounted-value"
    path.chmod(0o644)
    with pytest.raises(ConfigurationError):
        load_secrets(env)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    env["QTRADE_SECRETS_FILE"] = str(link)
    with pytest.raises(ConfigurationError):
        load_secrets(env)
    path.write_text(json.dumps({"QTRADE_ENV": "local"}))
    env["QTRADE_SECRETS_FILE"] = str(path)
    with pytest.raises(ConfigurationError):
        load_secrets(env)
    with pytest.raises(ConfigurationError):
        load_secrets({"QTRADE_ENV": "production"})


def test_release_record_refuses_corruption_environment_mismatch_and_overwrite(
    tmp_path, monkeypatch
):
    from qtrade_composition.releases import SERVICES, _write_new, read_record

    path = Path(tmp_path / "release.json")
    record = {
        "schema_version": 1,
        "environment": "local",
        "live_enabled": False,
        "images": {name: "sha256:" + "a" * 64 for name in SERVICES},
    }
    monkeypatch.setenv("QTRADE_ENV", "local")
    _write_new(path, record)
    assert read_record(path) == record
    with pytest.raises(FileExistsError):
        _write_new(path, record)
    monkeypatch.setenv("QTRADE_ENV", "production")
    with pytest.raises(ValueError):
        read_record(path)
    monkeypatch.setenv("QTRADE_ENV", "local")
    document = json.loads(path.read_text())
    document["images"]["api"] = "sha256:" + "b" * 64
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        read_record(path)
