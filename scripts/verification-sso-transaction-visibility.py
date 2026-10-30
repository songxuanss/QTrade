"""Isolated timing probe of the installed SSO database dependency.

Run with .tools/sso-venv and PYTHONPATH=../sso/src. Creates only a disposable
SQLite file and a probe app; never touches the real identity database or users.
"""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sso.webapp import get_db


async def check(root: Path, early: bool) -> dict[str, int | bool]:
    engine = create_engine(f"sqlite:///{root / ('early.db' if early else 'request.db')}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE visibility_probe (id integer PRIMARY KEY)"))
    app = FastAPI()
    app.state.session_factory = sessionmaker(bind=engine)
    dependency = Depends(get_db, scope="function") if early else Depends(get_db)

    @app.post("/publish")
    def publish(db: Session = dependency):
        db.execute(text("INSERT INTO visibility_probe(id) VALUES(1)"))
        return RedirectResponse("/next", status_code=303)

    observed = []

    async def boundary(scope, receive, send):
        async def checked_send(message):
            if message["type"] == "http.response.start":
                with engine.connect() as connection:
                    observed.append(
                        connection.scalar(text("SELECT count(*) FROM visibility_probe"))
                    )
            await send(message)

        await app(scope, receive, checked_send)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=boundary), base_url="http://probe.test"
        ) as client:
            result = await client.post("/publish", follow_redirects=False)
        with engine.connect() as connection:
            after = connection.scalar(text("SELECT count(*) FROM visibility_probe"))
        assert result.status_code == 303 and len(observed) == 1 and after == 1
        assert observed[0] == (1 if early else 0)
        return {
            "function_scope": early,
            "visible_when_headers_sent": observed[0],
            "visible_after_request": after,
        }
    finally:
        engine.dispose()


async def main() -> None:
    with TemporaryDirectory(prefix="qtrade-sso-visibility-") as directory:
        results = [await check(Path(directory), early) for early in (False, True)]
    print(json.dumps({"isolated_only": True, "results": results}))


if __name__ == "__main__":
    asyncio.run(main())
