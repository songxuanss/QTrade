"""Owned non-live workspace: real timer/provider/Worker, fixture plan creation.

Run prepare, then verify after its printed due time, and always disable. No
credentials leave the collector container; no live trading is enabled.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".tools/verification-local/schedule-live.json"


def container(code):
    result = subprocess.run(
        ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
        input=code,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError("schedule verification failed; private details withheld")
    return json.loads(result.stdout)


def main():
    action = sys.argv[1]
    if action == "prepare":
        if STATE.exists() and not json.loads(STATE.read_text()).get("disabled"):
            raise RuntimeError("owned verification schedule requires disable before replacement")
        scope = json.loads((STATE.parent / "budget-scope.json").read_text())["scope_id"]
        actor = json.loads((STATE.parent / "budget-actors.json").read_text())["admin"]
        value = container(f"""
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
from qtrade_collector.main import create_app
from qtrade_data_contracts.schedules import CollectionScheduleDraft, MarketScheduleSelection
app = create_app()
runner = app.state.schedule_runner
now = datetime.now(UTC)
local = now.astimezone(ZoneInfo("Asia/Shanghai")) + timedelta(minutes=1)
local = local.replace(second=0, microsecond=0)
draft = CollectionScheduleDraft(
    schema_version=1, scope_id=UUID({json.dumps(scope)}), actor_id=UUID({json.dumps(actor)}),
    schedule_id=uuid4(), name="自动日线实际来源验证（专用）",
    selection=MarketScheduleSelection(kind="market", source="mairui.history.bars",
        dataset_kind="bars", granularity="1d", instrument_ids=("601288.SH",)),
    cadence="daily", timezone="Asia/Shanghai", local_time=local.time(),
    lookback_days=7, approved_parts=20, approved_credits=500, automation_confirmed=True,
)
created = runner.controls.create(draft, now)
runner.controls.enable(draft.actor_id, draft.scope_id, draft.schedule_id, draft.revision)
print(json.dumps({{"scope_id":str(draft.scope_id),"actor_id":str(draft.actor_id),
    "schedule_id":str(draft.schedule_id),"revision":draft.revision,
    "due_at":created.next_due_at.isoformat(),"disabled":False}}))
""")
        STATE.write_text(json.dumps(value))
        STATE.chmod(0o600)
        print("Owned live schedule enabled; due UTC " + value["due_at"])
        return
    value = json.loads(STATE.read_text())
    scope, actor, schedule = (json.dumps(value[k]) for k in ("scope_id", "actor_id", "schedule_id"))
    prefix = f"""
import json
from uuid import UUID
from qtrade_collector.main import create_app
app=create_app()
runner=app.state.schedule_runner
scope,actor,schedule=UUID({scope}),UUID({actor}),UUID({schedule})
record=runner.controls.get(actor,scope,schedule)
"""
    if action == "disable":
        result = container(
            prefix
            + """
runner.controls.disable(actor,scope,schedule,record.revision)
print(json.dumps({"disabled":True}))
"""
        )
        value.update(result)
        STATE.write_text(json.dumps(value))
        STATE.chmod(0o600)
        print("Owned verification schedule disabled; stored data retained")
        return
    assert action == "verify"
    result = container(
        prefix
        + """
from qtrade_data_postgres.jobs import PostgresJobs
from qtrade_collector.config import configuration
repo=PostgresJobs(configuration()["DATA_COLLECTION_DATABASE_URL"])
with repo._connection() as conn:
    row=conn.execute("SELECT run_id FROM qtrade_data.collection_schedule_run "
        "WHERE scope_id=%s AND schedule_id=%s ORDER BY due_at DESC LIMIT 1",
        (scope,schedule)).fetchone()
if row is None:
    print(json.dumps({"state":"not_triggered","schedule_state":record.state}))
else:
    frozen=runner.controls.get_dispatch(actor,scope,row[0])
    job=app.state.collection_service.read(actor,scope,row[0])
    rows=sum(p["publication"]["record_count"] for p in job["parts"] if p.get("publication"))
    print(json.dumps({"state":job["state"],"job_id":str(row[0]),"schedule_state":record.state,
        "rows":rows,"parts":len(job["parts"]),"policy":frozen.policy_version,
        "start":frozen.dispatch.request.start.isoformat(),"end":frozen.dispatch.request.end.isoformat(),
        "publications":[p["publication"]["publication_id"]
            for p in job["parts"] if p.get("publication")]}))
"""
    )
    value["result"] = result
    STATE.write_text(json.dumps(value))
    STATE.chmod(0o600)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Schedule verification failed; private details withheld") from None
