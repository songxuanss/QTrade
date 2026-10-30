"""Non-destructive fixture workspace and temporary quota, never fake source usage."""

import json
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".tools/verification-local/budget-scope.json"
CONFIG = ROOT / ".env.collector"


def main():
    action = sys.argv[1]
    if action == "restore":
        state = json.loads(STATE.read_text())
        current = json.loads(dotenv_values(CONFIG).get("COLLECTOR_SCOPE_LIMITS_JSON") or "{}")
        current.pop(state["scope_id"], None)
        lines = CONFIG.read_text().splitlines()
        lines = [v for v in lines if not v.startswith("COLLECTOR_SCOPE_LIMITS_JSON=")]
        # Only remove this runner's override; preserve independently edited
        # tenant limits rather than restoring a stale whole configuration.
        if current:
            lines.append(
                "COLLECTOR_SCOPE_LIMITS_JSON=" + json.dumps(current, separators=(",", ":"))
            )
        elif state["original_limits"] is not None:
            lines.append("COLLECTOR_SCOPE_LIMITS_JSON={}")
        CONFIG.write_text("\n".join(lines) + "\n")
        CONFIG.chmod(0o600)
        print("Original scope quota configuration restored; deploy collector to activate")
        return
    assert action == "prepare"
    procstate = json.loads((ROOT / ".tools/verification-local/processes.json").read_text())
    entry = next(v for v in procstate if v["name"] == "api")
    proc = Path("/proc") / str(entry["pid"])
    assert proc.joinpath("stat").read_text().split(") ", 1)[1].split()[19] == str(entry["identity"])
    env = dict(
        p.split("=", 1)
        for p in proc.joinpath("environ").read_bytes().decode().split("\0")
        if "=" in p
    )
    config = dotenv_values(CONFIG)
    actors = json.loads((ROOT / ".tools/verification-local/budget-actors.json").read_text())
    state = (
        json.loads(STATE.read_text())
        if STATE.exists()
        else {
            "scope_id": str(uuid4()),
            "original_limits": config.get("COLLECTOR_SCOPE_LIMITS_JSON"),
        }
    )
    with psycopg.connect(env["DATABASE_URL"]) as conn:
        admin = conn.execute(
            "SELECT user_id FROM qtrade_user WHERE user_id=%s", (actors["admin"],)
        ).fetchall()
        viewer = conn.execute(
            "SELECT user_id FROM qtrade_user WHERE user_id=%s", (actors["reader"],)
        ).fetchall()
        assert len(admin) == len(viewer) == 1
        conn.execute(
            "INSERT INTO workspace(workspace_id,name,kind,owner_id,live_trading_enabled) "
            "VALUES(%s,'采集预算验证（专用）','organization',%s,false) ON CONFLICT DO NOTHING",
            (state["scope_id"], admin[0][0]),
        )
        for user, role in ((admin[0][0], "admin"), (viewer[0][0], "risk_manager")):
            conn.execute(
                "INSERT INTO membership(workspace_id,user_id,role,active,created_by) "
                "VALUES(%s,%s,%s,true,%s) ON CONFLICT DO NOTHING",
                (state["scope_id"], user, role, admin[0][0]),
            )
    limits = json.loads(state["original_limits"] or "{}")
    assert state["scope_id"] not in limits
    limits[state["scope_id"]] = {"daily_credits": 1, "concurrency": 1}
    lines = [
        v
        for v in CONFIG.read_text().splitlines()
        if not v.startswith("COLLECTOR_SCOPE_LIMITS_JSON=")
    ]
    lines.append("COLLECTOR_SCOPE_LIMITS_JSON=" + json.dumps(limits, separators=(",", ":")))
    CONFIG.write_text("\n".join(lines) + "\n")
    CONFIG.chmod(0o600)
    STATE.write_text(json.dumps(state))
    STATE.chmod(0o600)
    print("Dedicated non-live fixture workspace prepared; only its quota is overridden")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Budget validation setup failed; private details withheld") from None
