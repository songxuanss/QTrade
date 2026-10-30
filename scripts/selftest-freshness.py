"""Run freshness/alert integration tests in a new disposable local collector database.

Requires compose.collector.local.yaml's loopback PostgreSQL port 15436. Private
container credentials stay in memory; cleanup drops only the random owned DB.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

root = Path(__file__).resolve().parents[1]
info = json.loads(
    subprocess.check_output(["docker", "inspect", "qtrade-collector-collector-db-1"])
)[0]
env = dict(v.split("=", 1) for v in info["Config"]["Env"])
admin = dict(
    host="127.0.0.1",
    port=15436,
    user=env["POSTGRES_USER"],
    password=env["POSTGRES_PASSWORD"],
    dbname="postgres",
)
name = "research_test_" + uuid4().hex
with psycopg.connect(**admin, autocommit=True) as conn:
    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
try:
    url = psycopg.conninfo.make_conninfo(**{**admin, "dbname": name})
    paths = [
        "tests/test_freshness_health.py",
        "tests/test_freshness_health_bridge.py",
        "tests/test_freshness_health_postgres.py",
        "tests/test_freshness_watch_postgres.py",
        "tests/test_freshness_watch_bridge.py",
        "tests/test_collector_freshness_consumer_postgres.py",
        "tests/test_freshness_delivery_postgres.py",
        "tests/test_freshness_notice.py",
        "tests/test_freshness_notice_postgres.py",
        "tests/test_freshness_refresh_postgres.py",
        "tests/test_freshness_routes_postgres.py",
        "tests/test_collection_freshness_postgres.py",
        "tests/test_collection_freshness.py",
        "tests/test_freshness_check.py",
        "tests/test_freshness_bridge.py",
        "tests/test_freshness_store.py",
        "tests/test_daily_freshness_evidence.py",
        "tests/test_collector_http.py",
        "tests/test_architecture.py",
    ]
    code = subprocess.call(
        [sys.executable, "-m", "pytest", *paths],
        cwd=root,
        env={**os.environ, "QTRADE_TEST_RESEARCH_DATABASE_URL": url},
    )
finally:
    with psycopg.connect(**admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
sys.exit(code)
