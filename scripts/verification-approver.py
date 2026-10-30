"""Prepare a second real SSO identity solely in the isolated verification database."""

import json
import secrets
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sso.services.users import create_user

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / ".tools/verification-local/approver.json"
if not path.exists():
    state = json.loads((ROOT / ".tools/sso-local/state.json").read_text())
    assert state["databases"][0].startswith("qtrade_sso_test_")
    password = secrets.token_urlsafe(24)
    email = "qtrade-approver@example.test"
    engine = create_engine(state["sso_url"])
    with Session(engine) as session:
        create_user(
            session,
            email=email,
            display_name="QTrade Verification Approver",
            password=password,
            role="owner",
        )
        session.commit()
    engine.dispose()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.touch(mode=0o600)
    path.write_text(json.dumps({"email": email, "password": password}))
    path.chmod(0o600)
print("Second isolated SSO verification identity is ready")
