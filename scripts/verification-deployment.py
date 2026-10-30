"""Real startup rejection and local immutable-image rollback drill; no live orders."""

import argparse
import base64
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
container_environment = runpy.run_path(str(ROOT / "scripts/verification-local.py"))[
    "container_environment"
]


def environment() -> dict[str, str]:
    result = {**os.environ, "QTRADE_ENV": "local", "QTRADE_LIVE_TRADING_ENABLED": "false"}
    result["PYTHONPATH"] = os.pathsep.join(
        str(path / "src")
        for group in ("apps", "packages", "adapters")
        for path in (ROOT / group).iterdir()
        if (path / "src").is_dir()
    )
    for service, variable in (("postgres", "DATABASE_URL"), ("market-db", "MARKET_DATABASE_URL")):
        address, values = container_environment(service)
        result[variable] = (
            f"postgresql://{quote(values['POSTGRES_USER'], safe='')}:"
            f"{quote(values['POSTGRES_PASSWORD'], safe='')}@{address}:5432/{values['POSTGRES_DB']}"
        )
    result["QTRADE_CONFIG_VERSION"] = "local.release.drill.v1"
    return result


def rejection_checks(env: dict[str, str]) -> None:
    folder = ROOT / ".tools/releases"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = folder / "startup.secrets.json"
    private.write_text(
        json.dumps({"QTRADE_BFF_SECRET": "private-startup-verification-" + uuid4().hex})
    )
    private.chmod(0o600)
    try:
        for name, changes in (
            ("missing secrets", {"QTRADE_ENV": "production", "QTRADE_SECRETS_FILE": ""}),
            (
                "HTTP issuer",
                {
                    "QTRADE_ENV": "production",
                    "QTRADE_SECRETS_FILE": str(private),
                    "QTRADE_OIDC_ISSUER": "http://identity.invalid",
                },
            ),
            ("Live flag", {"QTRADE_LIVE_TRADING_ENABLED": "true"}),
            ("invalid flag", {"QTRADE_INSECURE_LOCAL_COOKIES": "yes"}),
        ):
            for module in ("qtrade_api.main", "qtrade_worker.main", "qtrade_realtime.main"):
                result = subprocess.run(
                    [sys.executable, "-c", f"import {module}"],
                    cwd=ROOT,
                    env={**env, **changes},
                    capture_output=True,
                    text=True,
                )
                assert result.returncode != 0, f"{module} accepted {name}"
                assert "ConfigurationError" in result.stderr
                assert "private-startup-verification-" not in result.stderr
            print(f"PASS: API/Worker/Realtime reject {name} before serving traffic")
        result = subprocess.run(
            ["node", "tests/serve-production.mjs"],
            cwd=ROOT / "apps/web",
            env={
                **env,
                "QTRADE_ENV": "production",
                "QTRADE_SECRETS_FILE": "",
                "PORT": "3190",
                "HOSTNAME": "127.0.0.1",
            },
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode != 0 and "QTRADE_SECRETS_FILE" in result.stderr
        print("PASS: production Web rejects missing private secrets before readiness")
        role = "release_check_" + uuid4().hex
        database = role
        password = base64.b64encode(os.urandom(32)).decode()
        from urllib.parse import urlsplit, urlunsplit

        address = urlsplit(env["DATABASE_URL"])
        with psycopg.connect(env["DATABASE_URL"], autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
            try:
                key = base64.b64encode(os.urandom(32)).decode()
                private.write_text(
                    json.dumps(
                        {
                            "QTRADE_BFF_SECRET": key,
                            "QTRADE_OIDC_CLIENT_SECRET": key,
                            "QTRADE_OIDC_TRANSACTION_KEY": key,
                            "QTRADE_SESSION_KEYS_JSON": json.dumps({"v1": key}),
                            "QTRADE_SESSION_PRIMARY_KEY": "v1",
                            "DATABASE_URL": urlunsplit(
                                (
                                    "postgresql",
                            f"{role}:{quote(password, safe='')}"
                            f"@{address.hostname}:{address.port}",
                                    "/" + database,
                                    "",
                                    "",
                                )
                            ),
                            "MARKET_DATABASE_URL": "postgresql://check:private@127.0.0.1:1/market",
                            "REDIS_URL": "redis://cache.invalid/0",
                        }
                    )
                )
                protected = {
                    **env,
                    "QTRADE_ENV": "production",
                    "QTRADE_SECRETS_FILE": str(private),
                    "QTRADE_OIDC_CLIENT_ID": "qtrade",
                    "QTRADE_OIDC_ISSUER": "https://identity.invalid",
                    "QTRADE_PUBLIC_ORIGIN": "https://trade.invalid",
                    "QTRADE_API_PUBLIC_ORIGIN": "https://api.invalid",
                    "OBJECT_STORE_URL": "https://objects.invalid",
                }
                for module in ("qtrade_api.main", "qtrade_worker.main", "qtrade_realtime.main"):
                    result = subprocess.run(
                        [sys.executable, "-c", f"import {module}"],
                        env=protected,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    assert result.returncode != 0 and "MigrationError" in result.stderr
                    assert password not in result.stderr and key not in result.stderr
                print(
                    "PASS: protected services reject an actual PostgreSQL database "
                    "missing migrations"
                )
            finally:
                conn.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
                )
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
    finally:
        private.unlink(missing_ok=True)


def snapshot(env: dict[str, str]) -> dict[str, str]:
    from hashlib import sha256

    from psycopg import sql

    results = {}
    for label, variable, schema in (
        ("domain", "DATABASE_URL", "qtrade"),
        ("market", "MARKET_DATABASE_URL", "qtrade_market"),
    ):
        with psycopg.connect(env[variable]) as conn:
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s "
                "AND table_type='BASE TABLE' ORDER BY table_name",
                (schema,),
            ).fetchall()
            for (name,) in tables:
                # Sorted row hashes compare persisted content without displaying private records.
                rows = conn.execute(
                    sql.SQL(
                        "SELECT row_to_json(t)::text FROM {}.{} t ORDER BY row_to_json(t)::text"
                    ).format(sql.Identifier(schema), sql.Identifier(name))
                ).fetchall()
                results[f"{label}.{name}"] = sha256(json.dumps(rows).encode()).hexdigest()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("startup", "baseline", "rollback"))
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    env = environment()
    sys.path[:0] = env["PYTHONPATH"].split(os.pathsep)
    if args.operation == "startup":
        rejection_checks(env)
        return
    if args.record is None:
        parser.error("--record is required")
    os.environ.update(env)
    from qtrade_composition.releases import plan_rollback, read_record, record_release, rollback

    if args.operation == "baseline":
        record_release(args.record)
        print("PASS: healthy committed-image release recorded with both migration histories")
        return
    record = read_record(args.record)
    current = json.loads(
        subprocess.check_output(
            [
                "docker",
                "inspect",
                subprocess.check_output(
                    ["docker", "compose", "ps", "-q", "api"], text=True
                ).strip(),
            ],
            text=True,
        )
    )[0]["Image"]
    assert current != record["images"]["api"], "drill requires a different deployed image"
    before = snapshot(env)
    prefix = args.record.parent / f"rollback-{uuid4().hex}"
    plan_rollback(args.record, prefix.with_suffix(".preflight.json"))
    subprocess.run(["docker", "compose", "stop", "api"], check=True, capture_output=True)
    try:
        try:
            httpx.get("http://127.0.0.1:18000/health", timeout=2).raise_for_status()
        except httpx.HTTPError:
            pass
        else:
            raise AssertionError("drill did not make the API unavailable")
        rollback(
            args.record, prefix.with_suffix(".compose.json"), prefix.with_suffix(".receipt.json")
        )
        assert httpx.get("http://127.0.0.1:18000/ready", timeout=20).status_code == 200
        assert snapshot(env) == before
        print(
            "PASS: unavailable API rolled back to previous immutable images; "
            "persisted rows unchanged"
        )
    except BaseException:
        subprocess.run(
            ["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "240"],
            capture_output=True,
        )
        raise


if __name__ == "__main__":
    main()
