"""Exercise the independent data schema in an ephemeral DB, without QTrade tables."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

root = Path(__file__).resolve().parents[1]
container = subprocess.check_output(
    ["docker", "compose", "ps", "-q", "postgres"], cwd=root, text=True
).strip()
info = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
address = next(iter(info["NetworkSettings"]["Networks"].values()))["IPAddress"]
env = dict(v.split("=", 1) for v in info["Config"]["Env"])
admin = dict(
    host=address,
    dbname=env["POSTGRES_DB"],
    user=env["POSTGRES_USER"],
    password=env["POSTGRES_PASSWORD"],
)
database = "qtrade_data_test_" + uuid4().hex
with psycopg.connect(**admin, autocommit=True) as conn:
    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
try:
    test_url = psycopg.conninfo.make_conninfo(**{**admin, "dbname": database})
    result = subprocess.run(
        [str(root / ".venv/bin/python"), "-m", "pytest", "tests/test_data_postgres.py"],
        cwd=root,
        env={**os.environ, "QTRADE_TEST_DATA_URL": test_url},
        check=False,
    )
finally:
    with psycopg.connect(**admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
raise SystemExit(result.returncode)
