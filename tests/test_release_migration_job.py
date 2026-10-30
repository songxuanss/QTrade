"""Reject stale/failed migration jobs even when the application is healthy."""

import json

import pytest

from qtrade_composition import releases


@pytest.mark.parametrize(
    "state,image,accepted",
    [
        ({"Status": "exited", "ExitCode": 0}, "sha256:api", True),
        ({"Status": "exited", "ExitCode": 0}, "sha256:old", False),
        ({"Status": "exited", "ExitCode": 1}, "sha256:api", False),
        ({"Status": "running", "ExitCode": 0}, "sha256:api", False),
    ],
)
def test_release_requires_successful_job_from_exact_api_image(monkeypatch, state, image, accepted):
    calls = []

    def command(args):
        calls.append(args)
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([{"State": state, "Image": image}])
        return "migration-container"

    monkeypatch.setattr(releases, "_command", command)
    if accepted:
        releases._verify_migration_job("sha256:api", base="protected.yaml")
    else:
        with pytest.raises(ValueError, match="exact API image"):
            releases._verify_migration_job("sha256:api", base="protected.yaml")
    assert calls[0] == ["docker", "compose", "-f", "protected.yaml", "ps", "-a", "-q", "migrate"]


def test_release_rejects_missing_migration_job(monkeypatch):
    monkeypatch.setattr(releases, "_command", lambda args: "")
    with pytest.raises(ValueError, match="completed migration job"):
        releases._verify_migration_job("sha256:api")


def test_rollback_plan_pins_migration_to_retained_api_image(monkeypatch, tmp_path):
    images = {service: "sha256:" + "a" * 64 for service in releases.SERVICES}
    record = {"images": images, "migrations": {}, "configuration_version": "test"}
    monkeypatch.setenv("QTRADE_CONFIG_VERSION", "test")
    monkeypatch.setenv("QTRADE_ENV", "local")
    monkeypatch.setattr(releases, "validate_startup", lambda: None)
    monkeypatch.setattr(releases, "read_record", lambda path: record)
    monkeypatch.setattr(releases, "_migrations", lambda: {})

    def command(args):
        if "config" in args:
            return json.dumps(
                {
                    "services": {
                        service: {"environment": {}} for service in (*releases.SERVICES, "migrate")
                    }
                }
            )
        return ""

    monkeypatch.setattr(releases, "_command", command)
    override = tmp_path / "override.json"
    releases.plan_rollback(tmp_path / "record.json", override)
    assert json.loads(override.read_text())["services"]["migrate"] == {
        "image": images["api"],
        "pull_policy": "never",
    }
