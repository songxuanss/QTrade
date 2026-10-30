"""Independent, checksum-locked data schema; never reads QTrade migration tables."""

import os
from hashlib import sha256
from pathlib import Path

import psycopg

DIRECTORY = Path(__file__).with_name("migrations")


def apply(database_url: str) -> tuple[str, ...]:
    applied: list[str] = []
    with psycopg.connect(database_url) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(794616387331)")
        conn.execute("CREATE SCHEMA IF NOT EXISTS qtrade_data")
        conn.execute("""CREATE TABLE IF NOT EXISTS qtrade_data.schema_migration(
            version text PRIMARY KEY,checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())""")
        stored: dict[str, str] = {}
        for version, checksum in conn.execute(
            "SELECT version,checksum FROM qtrade_data.schema_migration ORDER BY version"
        ).fetchall():
            if not isinstance(version, str) or not isinstance(checksum, str):
                raise ValueError("invalid data migration ledger")
            stored[version] = checksum
        files = sorted(DIRECTORY.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        if not files or set(stored) - {p.name for p in files}:
            raise ValueError("unknown data migration")
        for path in files:
            raw = path.read_bytes()
            digest = sha256(raw).hexdigest()
            if path.name in stored:
                if stored[path.name] != digest:
                    raise ValueError("data migration checksum mismatch")
                continue
            conn.execute(raw.decode())
            conn.execute(
                "INSERT INTO qtrade_data.schema_migration(version,checksum) VALUES(%s,%s)",
                (path.name, digest),
            )
            applied.append(path.name)
    return tuple(applied)


if __name__ == "__main__":
    applied = apply(os.environ["DATA_COLLECTION_DATABASE_URL"])
    print("Data migrations applied: " + (", ".join(applied) or "already current"))
