"""Ordered, checksum-locked, transactional PostgreSQL migrations.

An advisory transaction lock serializes concurrent startup. A failed migration
rolls back the entire invocation. Existing migration files must never be edited;
forward fixes are new files. Schema is explicit to support isolated integration tests.
"""

import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import psycopg
from psycopg import sql

MIGRATIONS = Path(__file__).with_name("migrations")
LOCK_ID = 0x515452414445


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    content: str
    checksum: str


def discover(directory: Path = MIGRATIONS) -> tuple[Migration, ...]:
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = re.fullmatch(r"([0-9]{4})_([a-z0-9_]+)\.sql", path.name)
        if match is None:
            raise MigrationError("invalid migration filename")
        content = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(int(match[1]), match[2], content, sha256(content.encode()).hexdigest())
        )
    versions = [item.version for item in migrations]
    if not versions or versions != list(range(1, len(versions) + 1)):
        raise MigrationError("migration versions must be contiguous starting at 1")
    return tuple(migrations)


def apply(
    database_url: str, *, schema: str = "qtrade", directory: Path = MIGRATIONS
) -> tuple[int, ...]:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise MigrationError("invalid schema name")
    migrations = discover(directory)
    applied: list[int] = []
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            connection.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
            )
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(schema))
            )
            connection.execute("""
                CREATE TABLE IF NOT EXISTS schema_migration (
                    version integer PRIMARY KEY,
                    name text NOT NULL,
                    checksum char(64) NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            existing: dict[int, str] = dict(
                connection.execute("SELECT version, checksum FROM schema_migration").fetchall()
            )
            known = {item.version for item in migrations}
            if existing.keys() - known:
                raise MigrationError("database migration is newer than this application")
            for migration in migrations:
                if migration.version in existing:
                    if existing[migration.version] != migration.checksum:
                        raise MigrationError("applied migration checksum changed")
                    continue
                connection.execute(migration.content)
                connection.execute(
                    "INSERT INTO schema_migration(version, name, checksum) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )
                applied.append(migration.version)
    return tuple(applied)


def verify(
    database_url: str, *, schema: str = "qtrade", directory: Path = MIGRATIONS
) -> tuple[Migration, ...]:
    """Verify an exact schema without creating, migrating or repairing anything."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise MigrationError("invalid schema name")
    expected = discover(directory)
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout='3000ms'")
            rows = connection.execute(
                sql.SQL(
                    "SELECT version,name,checksum FROM {}.schema_migration ORDER BY version"
                ).format(sql.Identifier(schema))
            ).fetchall()
    except psycopg.Error:
        raise MigrationError(
            "schema verification unavailable; apply migrations before startup"
        ) from None
    if rows != [(item.version, item.name, item.checksum) for item in expected]:
        raise MigrationError(
            "schema does not match application migrations; startup/rollback refused"
        )
    return expected


if __name__ == "__main__":
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    result = apply(url)
    print(f"Applied migrations: {result or 'already current'}")
