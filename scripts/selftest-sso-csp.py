"""Verify the review patch without modifying the sibling checkout.

Run with .tools/sso-venv/bin/python; no real credentials or database are required.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="qtrade-sso-csp-") as directory:
        staging = Path(directory)
        shutil.copytree(ROOT.parent / "sso/src/sso", staging / "src/sso")
        webapp = staging / "src/sso/webapp.py"
        if "def transaction_form_origin(" not in webapp.read_text():
            subprocess.run(
                ["git", "apply", str(ROOT / "docs/integration/sso-csp.patch")],
                cwd=staging,
                check=True,
            )
        sys.path.insert(0, str(staging / "src"))
        from sso.webapp import transaction_form_origin

        identifier = uuid4()
        target = "https://qtrade.example.test/auth/callback"
        grant = SimpleNamespace(
            denied=False,
            consumed_at=None,
            code_hash=None,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            client_id="qtrade",
            redirect_uri=target,
        )
        client = SimpleNamespace(is_disabled=False, redirect_uris=[target])
        factory = MagicMock()
        db = factory.return_value.__enter__.return_value
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    session_factory=factory,
                    production=True,
                )
            )
        )

        def check(name="consent.html", txn=str(identifier)):
            db.get.side_effect = [grant, client]
            return transaction_form_origin(request, name, {"txn": txn})

        for name in ("login.html", "consent.html"):
            assert check(name) == "https://qtrade.example.test"
        assert check("message.html") is None
        assert check(txn="invalid") is None
        assert check(txn="https://evil.test") is None
        for field, value in (
            ("denied", True),
            ("consumed_at", datetime.now(UTC)),
            ("code_hash", "issued"),
            ("expires_at", datetime.now(UTC) - timedelta(seconds=1)),
            ("created_at", datetime.now(UTC) - timedelta(minutes=11)),
            ("created_at", datetime.now(UTC) + timedelta(minutes=1)),
        ):
            previous = getattr(grant, field)
            setattr(grant, field, value)
            assert check() is None
            setattr(grant, field, previous)
        client.is_disabled = True
        assert check() is None
        client.is_disabled = False
        grant.redirect_uri = "https://evil.test/callback"
        assert check() is None
        for invalid in (
            "https://user:password@qtrade.example.test/callback",
            "https://qtrade.example.test/callback#fragment",
            "https://qtrade.example.test;form-action/callback",
            "javascript:alert(1)",
            "http://qtrade.example.test/callback",
            "http://127.0.0.1:3120/auth/callback",
        ):
            grant.redirect_uri = invalid
            client.redirect_uris = [invalid]
            assert check() is None
        request.app.state.production = False
        assert check() == "http://127.0.0.1:3120"
        if "--browser" in sys.argv[1:]:
            subprocess.run(
                ["npx", "playwright", "test", "--config", "playwright.sso.config.ts"],
                cwd=ROOT / "apps/web",
                env={**os.environ, "PYTHONPATH": str(staging / "src")},
                check=True,
            )
    print("PASS: registered transaction origins; unrelated, invalid, expired and disabled cases")


if __name__ == "__main__":
    main()
