"""Verify real daily evidence and a first snapshot, without probing latest prices."""

import json
import subprocess
import textwrap
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
retest = json.loads(
    (ROOT / ".tools/verification-local/review/collection-retest-ui.json").read_text()
)
params = {"scope": retest["scope_id"], "jobs": [run["job_id"] for run in retest["runs"]]}
code = """
import json
from datetime import UTC,datetime
from pathlib import Path
from uuid import UUID
import httpx
from qtrade_collector.config import configuration
from qtrade_data_core.freshness_evidence import DailyPublicationEvidence
from qtrade_data_core.freshness import daily_expectation,evaluate
from qtrade_data_contracts.freshness import FreshnessEvidence,FreshnessResult
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.usage_execution import usage_scope
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_http.client import RemoteDataAuthorizer
from qtrade_data_postgres.jobs import PostgresJobs

config=configuration()
params=json.loads(PARAMS_JSON)
scope=UUID(params['scope'])
repo=PostgresJobs(config['DATA_COLLECTION_DATABASE_URL'])
archive=FileCollectionArchive(Path(config['COLLECTOR_ARCHIVE_ROOT']))
auth=RemoteDataAuthorizer(config['COLLECTOR_AUTHORIZER_URL'],config['COLLECTOR_SERVICE_TOKEN'],allow_http=True)
verifier=DailyPublicationEvidence(auth,repo,archive)
results=[]
proofs=[]
for job_id in params['jobs']:
    job=repo.job(scope,UUID(job_id))
    assert job['state']=='completed'
    part=next(p for p in job['parts'] if p['instrument_id']=='601288.SH')
    publication=repo.publication(scope,UUID(part['publication']['publication_id']))
    proof=verifier.verify(publication.actor_id,scope,publication.publication_id)
    assert proof.published.complete_through.isoformat()=='2025-12-31'
    assert proof.collected.observed_at<=proof.published.observed_at
    assert proof.collected.evidence_sha256==proof.published.evidence_sha256
    proofs.append((proof,publication.actor_id))
    results.append({'job_id':job_id,'publication_id':str(publication.publication_id),
        'complete_through':proof.published.complete_through.isoformat(),
        'collected_at':proof.collected.observed_at.isoformat(),
        'published_at':proof.published.observed_at.isoformat(),
        'evidence_sha256':proof.published.evidence_sha256})
# This controlled verification owns only the existing non-live verification
# scope. Never overwrite an existing snapshot, nor expose an unprotected writer.
from qtrade_collector.main import app
proof,actor=max(proofs,key=lambda p:(p[0].published.complete_through,p[0].published.observed_at))
snapshots=app.state.freshness_snapshots
try:
    snapshot=snapshots.latest(actor,proof.key)
    written=False
except DataFailure as error:
    if error.code!='not_found': raise
    now=datetime.now(UTC)
    provider=app.state.collection_service.providers[0]
    with usage_scope(actor,scope):
        window=provider.default_range(proof.key.source,'1d',now)
    state=FreshnessEvidence(key=proof.key,source_health='unknown',
        collected=proof.collected,published=proof.published)
    snapshot=snapshots.record(actor,evaluate(proof.key,daily_expectation(proof.key,window),state,now))
    written=True
    assert snapshot.status=='unknown' and snapshot.reason=='source_unknown'
response=httpx.post('http://127.0.0.1:18100/v1/freshness/latest',
    headers={'Authorization':'Bearer '+config['COLLECTOR_SERVICE_TOKEN'],
        'X-Data-Actor':str(actor),'X-Data-Scope':str(scope)},
    json=proof.key.model_dump(mode='json'),timeout=30)
assert response.status_code==200
assert FreshnessResult.model_validate_json(response.text)==snapshot
print(json.dumps({'results':results,'snapshot_written':written,
    'snapshot':snapshot.model_dump(mode='json'),'public_query_verified':True}))
"""
# Sanitized diagnostics contain only exception type/code and source line numbers.
# Never forward the exception string, request URL, headers or private config.
code = (
    "try:\n"
    + textwrap.indent(code, "    ")
    + """
except Exception as error:
    import sys,traceback
    safe_codes={'unavailable','unverified','forbidden','not_found','unsupported','rate_limited','unauthorized','conflict'}
    code=getattr(error,'code',None)
    frames=traceback.extract_tb(error.__traceback__)
    print(json.dumps({'verification_error':{'type':type(error).__name__,
        'code':code if code in safe_codes else None,
        'frames':[{'file':Path(f.filename).name,'line':f.lineno} for f in frames[-4:]]}}))
    sys.exit(1)
"""
)
# The bounded supplier transport uses spawn. A guarded real file is required;
# stdin execution cannot bootstrap a child interpreter's __main__ module.
remote_path = f"/tmp/qtrade-daily-evidence-{uuid4().hex}.py"
program = (
    "PARAMS_JSON="
    + repr(json.dumps(params))
    + "\n"
    + "def main():\n"
    + textwrap.indent(code, "    ")
    + "\nif __name__ == '__main__':\n    main()\n"
)
write = subprocess.run(
    [
        "docker",
        "exec",
        "-i",
        "qtrade-collector-collector-1",
        "python",
        "-c",
        "import os,sys; f=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); "
        "os.write(f,sys.stdin.buffer.read()); os.close(f)",
        remote_path,
    ],
    input=program,
    text=True,
    capture_output=True,
)
if write.returncode:
    raise SystemExit("Unable to prepare owned guarded verification file")
try:
    result = subprocess.run(
        ["docker", "exec", "qtrade-collector-collector-1", "python", remote_path],
        text=True,
        capture_output=True,
    )
finally:
    subprocess.run(
        [
            "docker",
            "exec",
            "qtrade-collector-collector-1",
            "python",
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).unlink(missing_ok=True)",
            remote_path,
        ],
        capture_output=True,
        check=True,
    )
if result.returncode:
    # Only safe error type/code survives; never forward container traces/config.
    try:
        safe = json.loads(result.stdout)["verification_error"]
    except (ValueError, KeyError):
        safe = {"type": "unknown", "code": None}
    raise SystemExit("Daily publication evidence verification failed: " + json.dumps(safe))
values = json.loads(result.stdout)
assert len(values["results"]) == 3
path = ROOT / ".tools/verification-local/review/daily-freshness-evidence-results.json"
path.write_text(json.dumps(values | {"latest_price_supplier_probe": False}, indent=2))
path.chmod(0o600)
print(
    "PASS 3 real annual publications: raw/standard/quality replay and daily checkpoints; "
    "real evidence snapshot/public query; no latest-price supplier probe"
)
