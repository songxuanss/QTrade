"""Read safe stage timings and immutable object sizes for the fixed UI runs."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / ".tools/verification-local/review"


def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    if phase not in {"baseline", "retest"}:
        raise SystemExit("Invalid verification phase")
    ui = json.loads((DIRECTORY / f"collection-{phase}-ui.json").read_text())
    if not ui.get("completed_at"):
        raise SystemExit("Baseline UI is still running; do not summarize partial runs")
    # Logs are already allowlisted; never forward raw docker log text or env.
    raw = subprocess.check_output(
        ["docker", "logs", "qtrade-collector-collector-1"],
        stderr=subprocess.STDOUT,
    ).decode()
    events = []
    for line in raw.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    selected = []
    request_ids = {}
    for run in ui["runs"]:
        requests = [
            e
            for e in events
            if e.get("event") == "provider_request" and e.get("job_id") == run["job_id"]
        ]
        workers = [
            e
            for e in events
            if e.get("event") == "part_finished" and e.get("job_id") == run["job_id"]
        ]
        request_ids[run["job_id"]] = [
            e["usage_request_id"] for e in requests if e.get("usage_request_id")
        ]
        selected.append(
            {
                "size": run["size"],
                "job_id": run["job_id"],
                "state": run["state"],
                "request_events": len(requests),
                "provider_pipeline_ms": sum(e.get("duration_ms", 0) for e in requests),
                "pace_wait_ms": sum(e.get("pace_wait_ms", 0) for e in requests),
                "transport_known": sum(e.get("transport_ms") is not None for e in requests),
                "transport_ms": sum(e.get("transport_ms") or 0 for e in requests),
                "response_bytes": sum(e.get("response_bytes", 0) for e in requests),
                "worker_events": len(workers),
                "worker_ms": sum(e.get("duration_ms", 0) for e in workers),
            }
        )
    payload = {
        "scope": ui["scope_id"],
        "requests": request_ids,
        "runs": [
            {"job_id": r["job_id"], "parts": [p["part_id"] for p in r["parts"]]} for r in ui["runs"]
        ],
    }
    code = """
import json,sys
from pathlib import Path
from uuid import UUID
import psycopg
from qtrade_collector.config import configuration
value=json.load(sys.stdin);output=[]
with psycopg.connect(configuration()['DATA_COLLECTION_DATABASE_URL']) as conn:
 for run in value['runs']:
  files=[]
  for part in run['parts']:
   root=Path('/data/archives')/value['scope']/part
   files.extend(p for p in root.rglob('*') if p.is_file())
  row=conn.execute("SELECT count(*),coalesce(sum(credits),0),"
   "coalesce(sum(response_bytes),0),count(*) FILTER (WHERE outcome='failure') "
   "FROM qtrade_data.usage_request WHERE scope_id=%s AND request_id=ANY(%s::uuid[])",
   (UUID(value['scope']),value['requests'][run['job_id']])).fetchone()
  output.append({'job_id':run['job_id'],'object_files':len(files),
   'object_bytes':sum(p.stat().st_size for p in files),
   'ledger_requests':row[0],'ledger_credits':row[1],
   'ledger_response_bytes':int(row[2]),'ledger_failures':row[3]})
print(json.dumps(output))
"""
    # stdin cannot supply both the program and data to python -. Use -c only
    # for this read-only, non-spawning inspection, with JSON on the input pipe.
    result = subprocess.run(
        ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-c", code],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise SystemExit("Archive measurement failed; private details withheld")
    sizes = {r["job_id"]: r for r in json.loads(result.stdout)}
    for run in selected:
        run.update(sizes[run["job_id"]])
    output = {
        "timing_semantics": (
            "transport is child network/body reception; pipeline includes spawn/JSON/archive; "
            "pace is separate, not vendor invoice"
        ),
        "runs": selected,
    }
    target = DIRECTORY / f"collection-{phase}-metrics.json"
    target.write_text(json.dumps(output, indent=2))
    target.chmod(0o600)
    print(json.dumps(output))


if __name__ == "__main__":
    main()
