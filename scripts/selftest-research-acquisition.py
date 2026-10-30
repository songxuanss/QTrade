"""Run research acquisition/raw retention tests in a new disposable local collector database.

Requires compose.collector.local.yaml's loopback PostgreSQL port 15436. Private
container credentials stay in memory; cleanup drops only the random owned DB.
Pass --collection-only to rerun the collection/rejected-JSON group independently.
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
        "tests/test_research_acquisition_contract.py",
        "tests/test_research_acquisition_bridge.py",
        "tests/test_research_bridge.py",
        "tests/test_research_viewer_bridge.py",
        "tests/test_research_processing_contract.py",
        "tests/test_mairui_research.py",
        "tests/test_mairui_distributions.py",
    ],
    ["tests/test_research_acquisition_postgres.py"],
    ["tests/test_research_replay_postgres.py", "tests/test_research_replay_http.py"],
    ["tests/test_research_jobs_postgres.py"],
    ["tests/test_research_budget_wait_postgres.py"],
    ["tests/test_research_collection_postgres.py"],
    ["tests/test_research_evidence_postgres.py"],
    [
        "tests/test_collection_replay_postgres.py",
        "tests/test_supplier_json_persistence_postgres.py",
    ],
]
if sys.argv[1:] == ["--collection-only"]:
    groups = groups[-1:]
elif sys.argv[1:]:
    raise SystemExit("Supported option: --collection-only")
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
            env={**os.environ, "QTRADE_TEST_RESEARCH_DATABASE_URL": url},
        )
        if result:
            code = result
    finally:
        with psycopg.connect(**admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
sys.exit(code)
