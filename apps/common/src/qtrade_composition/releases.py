"""Immutable release records and exact-schema rollback planning for Compose."""

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import uuid4

from qtrade_composition.startup import validate_startup
from qtrade_postgres.market_migrate import DIRECTORY
from qtrade_postgres.migrate import verify

SERVICES = ("api", "web", "worker", "realtime", "strategy-runner")


def _command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        # Container environment and command diagnostics can include private settings.
        raise ValueError("deployment command failed; inspect private operator diagnostics")
    return result.stdout


def _migrations() -> dict[str, list[dict[str, object]]]:
    return {
        name: [
            {"version": item.version, "name": item.name, "checksum": item.checksum}
            for item in items
        ]
        for name, items in (
            ("domain", verify(os.environ["DATABASE_URL"])),
            (
                "market",
                verify(
                    os.environ["MARKET_DATABASE_URL"], schema="qtrade_market", directory=DIRECTORY
                ),
            ),
        )
    }


def _digest(document: dict[str, object]) -> str:
    return sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write_new(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        os.chmod(path, 0o600)
        json.dump({**document, "content_sha256": _digest(document)}, file, indent=2, sort_keys=True)
        file.write("\n")


def read_record(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text())
        digest = document.pop("content_sha256")
        if digest != _digest(document):
            raise ValueError
        if (
            document.get("schema_version") != 1
            or document.get("live_enabled") is not False
            or document.get("environment") != os.environ.get("QTRADE_ENV", "local")
            or not isinstance(document.get("images"), dict)
            or set(document["images"]) != set(SERVICES)
            or any(
                not re.fullmatch(r"sha256:[0-9a-f]{64}", image)
                for image in document["images"].values()
            )
        ):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("invalid or incompatible release record") from None
    return cast(dict[str, object], document)


def record_release(path: Path) -> None:
    validate_startup()
    if path.exists():
        raise ValueError("release record already exists")
    release_id = uuid4()
    commit = _command(["git", "rev-parse", "HEAD"]).strip()
    if _command(["git", "status", "--porcelain"]).strip():
        raise ValueError("release requires a clean, committed worktree")
    version = os.environ.get("QTRADE_CONFIG_VERSION")
    if not version or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", version):
        raise ValueError("QTRADE_CONFIG_VERSION must be a public configuration version")
    images = {}
    for service in SERVICES:
        container = _command(["docker", "compose", "ps", "-q", service]).strip()
        if not container:
            raise ValueError("release requires all running services")
        info = json.loads(_command(["docker", "inspect", container]))[0]
        if (
            not info["State"]["Running"]
            or info["State"].get("Health", {}).get("Status") != "healthy"
        ):
            raise ValueError("release requires healthy services")
        revision = (info["Config"].get("Labels") or {}).get("org.opencontainers.image.revision")
        if revision != commit:
            raise ValueError("release image revision does not match the committed worktree")
        configured = dict(value.split("=", 1) for value in info["Config"].get("Env", []))
        if os.environ.get("QTRADE_ENV") in {"staging", "production"} and (
            configured.get("QTRADE_ENV") != os.environ["QTRADE_ENV"]
            or configured.get("QTRADE_CONFIG_VERSION") != version
            or configured.get("QTRADE_LIVE_TRADING_ENABLED", "false") != "false"
        ):
            raise ValueError(
                "release service configuration does not match the protected deployment"
            )
        images[service] = info["Image"]
    _verify_migration_job(images["api"])
    retained = {}
    for service, image in images.items():
        alias = f"qtrade-retained:{release_id.hex}-{service}"
        _command(["docker", "image", "tag", image, alias])
        retained[service] = alias
    _write_new(
        path,
        {
            "schema_version": 1,
            "release_id": str(release_id),
            "created_at": datetime.now(UTC).isoformat(),
            "environment": os.environ.get("QTRADE_ENV", "local"),
            "application_commit": commit,
            "configuration_version": version,
            "live_enabled": False,
            "images": images,
            "retained_images": retained,
            "migrations": _migrations(),
        },
    )


def plan_rollback(record_path: Path, override_path: Path) -> dict[str, object]:
    validate_startup()
    document = read_record(record_path)
    if document["configuration_version"] != os.environ.get("QTRADE_CONFIG_VERSION"):
        raise ValueError("restore the recorded configuration version before rollback")
    if document["migrations"] != _migrations():
        # No implicit down migration: restore/forward-fix first and re-verify business data.
        raise ValueError("rollback migration compatibility check failed")
    images = document["images"]
    if not isinstance(images, dict):
        raise ValueError("invalid release images")
    for image in images.values():
        _command(["docker", "image", "inspect", image])
    # JSON is also a valid Compose YAML document. No private environment is written here.
    with override_path.open("x") as file:
        os.chmod(override_path, 0o600)
        json.dump(
            {
                "services": {
                    **{
                        name: {"image": image, "pull_policy": "never"}
                        for name, image in images.items()
                    },
                    "migrate": {"image": images["api"], "pull_policy": "never"},
                }
            },
            file,
            indent=2,
        )
    base = os.environ.get("QTRADE_COMPOSE_FILE", "compose.yaml")
    effective = json.loads(
        _command(
            [
                "docker",
                "compose",
                "-f",
                base,
                "-f",
                str(override_path),
                "config",
                "--format",
                "json",
            ]
        )
    )
    for service in (*SERVICES, "migrate"):
        settings = effective["services"][service].get("environment", {})
        if settings.get("QTRADE_LIVE_TRADING_ENABLED", "false") != "false":
            raise ValueError("rollback configuration must keep Live disabled")
        if os.environ.get("QTRADE_ENV") in {"staging", "production"} and (
            settings.get("QTRADE_ENV") != os.environ["QTRADE_ENV"]
            or settings.get("QTRADE_CONFIG_VERSION") != document["configuration_version"]
        ):
            raise ValueError("rollback Compose configuration version does not match the release")
    return document


def rollback(record_path: Path, override_path: Path, receipt_path: Path) -> None:
    if receipt_path.exists():
        raise ValueError("rollback receipt already exists")
    document = plan_rollback(record_path, override_path)
    base = os.environ.get("QTRADE_COMPOSE_FILE", "compose.yaml")
    _command(
        [
            "docker",
            "compose",
            "-f",
            base,
            "-f",
            str(override_path),
            "up",
            "-d",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "240",
        ]
    )
    # Confirm exact images after health acceptance; an unchanged/mismatched tag is not success.
    images = document["images"]
    assert isinstance(images, dict)
    for service, expected in images.items():
        container = _command(
            ["docker", "compose", "-f", base, "-f", str(override_path), "ps", "-q", service]
        ).strip()
        image = json.loads(_command(["docker", "inspect", container]))[0]["Image"]
        if image != expected:
            raise ValueError("rollback image verification failed")
    _verify_migration_job(images["api"], base=base, override=override_path)
    _write_new(
        receipt_path,
        {
            "schema_version": 1,
            "operation": "rollback",
            "release_id": document["release_id"],
            "occurred_at": datetime.now(UTC).isoformat(),
            "migrations": _migrations(),
            "configuration_version": document["configuration_version"],
            "images": images,
        },
    )


def _verify_migration_job(
    api_image: str, *, base: str | None = None, override: Path | None = None
) -> None:
    command = ["docker", "compose"]
    if base is not None:
        command += ["-f", base]
    if override is not None:
        command += ["-f", str(override)]
    container = _command([*command, "ps", "-a", "-q", "migrate"]).strip()
    if not container:
        raise ValueError("release requires a completed migration job")
    info = json.loads(_command(["docker", "inspect", container]))[0]
    if (
        info["State"]["Status"] != "exited"
        or info["State"]["ExitCode"] != 0
        or info["Image"] != api_image
    ):
        raise ValueError("migration job must succeed using the exact API image")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("record", "plan-rollback", "rollback"))
    parser.add_argument("record", type=Path)
    parser.add_argument("--override", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "record":
            record_release(args.record)
        elif args.override is None:
            raise ValueError("--override is required")
        elif args.operation == "plan-rollback":
            plan_rollback(args.record, args.override)
        elif args.receipt is None:
            raise ValueError("--receipt is required")
        else:
            rollback(args.record, args.override, args.receipt)
    except (ValueError, OSError, KeyError) as error:
        # Only our fixed messages reach operators; underlying commands are never printed.
        raise SystemExit(
            str(error) if isinstance(error, ValueError) else "release operation failed"
        ) from None
    print("Release operation verified")


if __name__ == "__main__":
    main()
