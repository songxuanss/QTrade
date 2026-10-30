"""Start the actual API against isolated integration state; never print credentials."""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
state = json.loads((ROOT / ".tools/sso-local/state.json").read_text())
os.environ.update(
    QTRADE_ENV="test",
    QTRADE_INSECURE_LOCAL_COOKIES="true",
    DATABASE_URL=state["api_url"],
    QTRADE_OIDC_ISSUER=state["issuer"],
    QTRADE_BFF_SECRET=state["bridge_secret"],
    QTRADE_SESSION_PRIMARY_KEY="integration",
    QTRADE_SESSION_KEYS_JSON=json.dumps({"integration": state["session_key"]}),
    PYTHONPATH=os.pathsep.join(
        str(path / "src")
        for path in [
            *(ROOT / "apps").iterdir(),
            *(ROOT / "packages").iterdir(),
            *(ROOT / "adapters").iterdir(),
        ]
        if (path / "src").is_dir()
    ),
)
os.execv(
    str(ROOT / ".venv/bin/python"),
    [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "uvicorn",
        "qtrade_api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "3122",
        "--no-access-log",
    ],
)
