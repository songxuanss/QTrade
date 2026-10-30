"""Separate checksum-locked TimescaleDB market-store migration entry point."""

import os
from pathlib import Path

from qtrade_postgres.migrate import apply

DIRECTORY = Path(__file__).with_name("market_migrations")


if __name__ == "__main__":
    url = os.environ.get("MARKET_DATABASE_URL")
    if not url:
        raise SystemExit("MARKET_DATABASE_URL is required")
    result = apply(url, schema="qtrade_market", directory=DIRECTORY)
    print(f"Applied market migrations: {result or 'already current'}")
