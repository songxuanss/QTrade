"""Connect the local Compose BFF to the existing persisted SSO/API deployment.

Run with .tools/sso-venv/bin/python. Writes only a Git-ignored private env file.
Does not change API databases, create identities, or disable authentication.
"""

import json
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sso.models import Client
from sso.services.clients import update_client

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:13000"


def configure() -> None:
    state = json.loads((ROOT / ".tools/sso-local/state.json").read_text())
    engine = create_engine(state["sso_url"])
    with Session(engine) as db:
        client = db.get(Client, state["client_id"])
        if client is None or client.is_disabled:
            raise RuntimeError("Existing local SSO client is unavailable")
        update_client(
            db,
            client,
            name=client.name,
            redirect_uris=list(dict.fromkeys([*client.redirect_uris, ORIGIN + "/auth/callback"])),
            post_logout_redirect_uris=list(
                dict.fromkeys([*client.post_logout_redirect_uris, ORIGIN + "/"])
            ),
            scopes=client.scopes,
            environment=client.environment,
            access_policy=client.access_policy,
            backchannel_logout_uri=client.backchannel_logout_uri or "",
            production=False,
        )
        db.commit()
    engine.dispose()
    values = {
        "QTRADE_PUBLIC_ORIGIN": ORIGIN,
        "QTRADE_API_ORIGIN": "http://127.0.0.1:3122",
        "QTRADE_API_PUBLIC_ORIGIN": "http://127.0.0.1:3122",
        "QTRADE_OIDC_ISSUER": state["issuer"],
        "QTRADE_OIDC_CLIENT_ID": state["client_id"],
        "QTRADE_OIDC_CLIENT_SECRET": state["client_secret"],
        "QTRADE_OIDC_TRANSACTION_KEY": state["transaction_key"],
        "QTRADE_BFF_SECRET": state["bridge_secret"],
        "QTRADE_OIDC_ALLOW_HTTP": "true",
        "QTRADE_INSECURE_LOCAL_COOKIES": "true",
    }
    path = ROOT / ".env.local-auth"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, "w") as output:
        for key, value in values.items():
            if any(char in value for char in "\r\n'"):
                raise RuntimeError("Invalid private deployment configuration")
            output.write(f"{key}='{value}'\n")
    print("Local Compose BFF configuration saved privately; existing SSO callbacks retained")


if __name__ == "__main__":
    configure()
