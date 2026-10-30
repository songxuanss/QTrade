"""Loopback-only browser fixture exercising the real session/cookie HTTP modules.

This is never included in production images. Identity and SSO tokens are test data;
there is no corresponding public session creation route in the application.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
for directory in (
    "apps/api",
    "packages/application",
    "packages/domain",
    "packages/contracts",
    "packages/runtime",
    "adapters/mock",
    "adapters/crypto",
):
    sys.path.insert(0, str(ROOT / directory / "src"))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from starlette.responses import HTMLResponse, RedirectResponse  # noqa: E402

from qtrade_api.sessions import session_router  # noqa: E402
from qtrade_application.sessions import SessionService, TokenSet  # noqa: E402
from qtrade_crypto.tokens import TokenCipher  # noqa: E402
from qtrade_domain.identity import Membership, Role, User, Workspace  # noqa: E402
from qtrade_domain.identity_port import LoginIdentity  # noqa: E402
from qtrade_mock.sessions import MockSessionStore  # noqa: E402
from qtrade_runtime.cookies import SessionCookie  # noqa: E402

now = datetime.now(UTC)
user, workspace = uuid4(), uuid4()
identity = LoginIdentity(
    User(user, "https://sso.test", "browser-subject", "Browser user", None, False, now, now),
    Workspace(workspace, "Personal", "personal", user, False, False, now),
    Membership(workspace, user, Role.ADMIN, True, user, now, now),
)
service = SessionService(MockSessionStore(), TokenCipher({"fixture": b"t" * 32}, primary="fixture"))
policy = SessionCookie("test", insecure_local=True)
app = FastAPI()
app.include_router(session_router(service, policy))


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/seed")
def seed():
    created = service.create(
        identity,
        TokenSet("browser-private-access", "browser-private-refresh", "browser-private-id"),
    )
    response = RedirectResponse("/page", status_code=303)
    policy.set(response, created.cookie, max_age=28800)
    return response


@app.get("/page")
def page():
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><head><title>Session privacy fixture</title></head>'
        "<body><main><h1>Session privacy</h1></main></body></html>"
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3101, log_level="warning")
