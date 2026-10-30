"""Isolated sibling-SSO integration environment; run with .tools/sso-venv/bin/python."""

import argparse
import base64
import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".tools/sso-local/state.json"


def postgres():
    container = subprocess.check_output(
        ["docker", "compose", "ps", "-q", "postgres"], cwd=ROOT, text=True
    ).strip()
    if not container:
        raise RuntimeError("start QTrade Compose PostgreSQL first")
    info = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
    env = dict(item.split("=", 1) for item in info["Config"]["Env"])
    host = next(iter(info["NetworkSettings"]["Networks"].values()))["IPAddress"]
    return (
        "postgresql://"
        + quote(env["POSTGRES_USER"], safe="")
        + ":"
        + quote(env["POSTGRES_PASSWORD"], safe="")
        + "@"
        + host
        + ":5432/"
    )


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(state, output)


def prepare():
    if STATE.exists():
        raise RuntimeError(
            "isolated environment already exists; use cleanup before preparing again"
        )
    prefix = postgres()
    names = ["qtrade_sso_test_" + uuid4().hex, "qtrade_rp_test_" + uuid4().hex]
    state = {"databases": names, "prefix": prefix}
    state.update(
        bridge_secret=secrets.token_urlsafe(32),
        session_key=base64.b64encode(secrets.token_bytes(32)).decode(),
        transaction_key=base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    save(state)
    with psycopg.connect(prefix + "postgres", autocommit=True) as connection:
        for name in names:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sso.migrate import run_migrations
    from sso.services.clients import create_client
    from sso.services.users import create_user

    sso_url = (prefix + names[0]).replace("postgresql://", "postgresql+psycopg://", 1)
    run_migrations(sso_url)
    password = secrets.token_urlsafe(24)
    engine = create_engine(sso_url)
    with Session(engine) as session:
        create_user(
            session,
            email="qtrade-integration@example.test",
            display_name="QTrade Test",
            password=password,
            role="owner",
        )
        client, secret = create_client(
            session,
            name="QTrade local integration",
            client_type="confidential",
            redirect_uris=["http://127.0.0.1:3120/auth/callback"],
            scopes=["openid", "email", "profile", "offline_access"],
            post_logout_redirect_uris=["http://127.0.0.1:3120/"],
            backchannel_logout_uri="http://127.0.0.1:3120/auth/backchannel-logout",
            environment="development",
            production=False,
        )
        session.commit()
        state.update(client_id=client.client_id, client_secret=secret, password=password)
    engine.dispose()
    state.update(sso_url=sso_url, api_url=prefix + names[1], issuer="http://127.0.0.1:3121")
    with STATE.open("w") as output:
        json.dump(state, output)
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "qtrade_postgres.migrate"],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": state["api_url"],
            "PYTHONPATH": str(ROOT / "adapters/postgres/src"),
        },
        check=True,
    )
    print("Prepared isolated SSO and QTrade databases; private configuration in .tools/sso-local")


def cleanup():
    state = json.loads(STATE.read_text())
    names = state["databases"]
    if len(names) != 2 or any(
        not re.fullmatch(r"qtrade_(sso|rp)_test_[0-9a-f]{32}", name) for name in names
    ):
        raise RuntimeError("refusing cleanup of non-test databases")
    with psycopg.connect(state["prefix"] + "postgres", autocommit=True) as connection:
        for name in names:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
    STATE.unlink()
    print("Removed isolated integration databases and private configuration")


def serve():
    import uvicorn
    from sso.config import Settings
    from sso.main import create_app

    state = json.loads(STATE.read_text())
    app = create_app(
        Settings(
            _env_file=None,
            issuer=state["issuer"],
            database_url=state["sso_url"],
            production=False,
            trust_proxy_depth=0,
            rate_limit_per_minute=10000,
        ),
        run_db_migrations=False,
    )
    uvicorn.run(app, host="127.0.0.1", port=3121, log_level="warning", access_log=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "serve", "cleanup"])
    arguments = parser.parse_args()
    {"prepare": prepare, "serve": serve, "cleanup": cleanup}[arguments.action]()
