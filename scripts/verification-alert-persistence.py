"""Check the actual evaluator and notification results for the browser-created rule."""

import json
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
state = json.loads((ROOT / ".tools/sso-local/state.json").read_text())
report = (ROOT / ".tools/verification-local/review/alerts-results.txt").read_text()
name = next(
    line.removeprefix("Rule: ") for line in report.splitlines() if line.startswith("Rule: ")
)
workspace = next(
    line.removeprefix("Workspace: ")
    for line in report.splitlines()
    if line.startswith("Workspace: ")
)
with psycopg.connect(state["api_url"]) as conn:
    row = conn.execute(
        "SELECT d.status,d.attempts,count(n.notification_id) FROM notification_delivery d "
        "LEFT JOIN in_app_notification n USING(workspace_id,notification_id) "
        "WHERE d.subject=%s AND d.workspace_id=%s GROUP BY d.status,d.attempts",
        (name, workspace),
    ).fetchone()
    assert row == ("delivered", 1, 1), "Worker did not deliver and persist the in-app notification"
    event = conn.execute(
        "SELECT status FROM alert_event WHERE title=%s AND workspace_id=%s", (name, workspace)
    ).fetchone()
    assert event == ("closed",), "UI event close did not persist"
print("PASS: actual database event close and Worker in-app notification delivery")
