"""Run prepared-conversion and queue/schedule tests in disposable collector DBs.

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
groups = [
    [
        "tests/test_normalization_inputs.py",
        "tests/test_bar_source_flags.py",
        "tests/test_independent_collection.py",
        "tests/test_collection_quality.py",
        "tests/test_collection_planning.py",
        "tests/test_mairui_cache_concurrency.py",
        "tests/test_collection_plan_guards.py",
        "tests/test_data_contracts.py",
    ],
    ["tests/test_collection_replay_postgres.py", "tests/test_collection_replay_http.py"],
    ["tests/test_independent_collection_postgres.py"],
    ["tests/test_collection_schedules_postgres.py"],
]

code = 0
for paths in groups:
    # Worker claims intentionally span all scopes. Each queue suite owns a
    # separate database so another suite's unfinished jobs cannot satisfy claims.
    name = "research_test_" + uuid4().hex
    with psycopg.connect(**admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        url = psycopg.conninfo.make_conninfo(**{**admin, "dbname": name})
        result = subprocess.call(
            [sys.executable, "-m", "pytest", *paths],
            cwd=root,
            env={
                **os.environ,
                "QTRADE_TEST_COLLECTOR_DATABASE_URL": url,
                "QTRADE_TEST_RESEARCH_DATABASE_URL": url,
            },
        )
        if result:
            code = result
    finally:
        with psycopg.connect(**admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
sys.exit(code)
