"""Exercise research/legacy storage in a newly created disposable database only."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = dotenv_values(ROOT / ".env.collector")
    info = json.loads(
        subprocess.check_output(["docker", "inspect", "qtrade-collector-collector-db-1"])
    )[0]
    address = info["NetworkSettings"]["Networks"]["qtrade-collector_default"]["IPAddress"]
    admin = make_conninfo(
        host=address, user="collector", dbname="collector", password=config["COLLECTOR_DB_PASSWORD"]
    )
    name = "research_test_" + uuid4().hex
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        test_url = make_conninfo(
            host=address, user="collector", dbname=name, password=config["COLLECTOR_DB_PASSWORD"]
        )
        env = dict(os.environ)
        env["QTRADE_TEST_RESEARCH_DATABASE_URL"] = test_url
        env["QTRADE_TEST_COLLECTOR_DATABASE_URL"] = test_url
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_research_evidence_postgres.py",
                "tests/test_independent_collection_postgres.py",
                "tests/test_research_publications_postgres.py",
                "tests/test_research_collection_postgres.py",
                "tests/test_research_jobs_postgres.py",
                "tests/test_usage_ledger_postgres.py",
                "tests/test_fair_dispatch_postgres.py",
                "tests/test_research_budget_wait_postgres.py",
                "tests/test_collection_schedules_postgres.py",
                "tests/test_schedule_runner_postgres.py",
                "tests/test_schedule_routes_postgres.py",
                "-q",
                "--tb=short",
            ],
            cwd=ROOT,
            env=env,
        )
    finally:
        # Only this invocation's generated database is eligible for disposal.
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
