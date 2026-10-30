"""Controlled real-evidence merge/replay; never relabel this as a new source probe."""

import json
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
original = json.loads(
    (ROOT / ".tools/verification-local/review/daily-freshness-evidence-results.json").read_text()
)["snapshot"]
baseline = json.loads(
    (ROOT / ".tools/verification-local/review/collection-retest-ui.json").read_text()
)
params = {"original": original, "publication": baseline["runs"][0]["parts"][0]["publication_id"]}
code = """
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID
import httpx,psycopg
from qtrade_collector.main import app
from qtrade_collector.config import configuration
from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.freshness import FreshnessEvidence,FreshnessResult
from qtrade_data_core.freshness import evaluate
from qtrade_data_core.freshness_evidence import DailyPublicationEvidence
from qtrade_data_core.freshness_refresh import FreshnessRefresh
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_postgres.freshness import PostgresFreshness
from qtrade_data_postgres.jobs import PostgresJobs

params=json.loads(PARAMS_JSON); config=configuration()
original=FreshnessResult.model_validate_json(json.dumps(params['original']))
repo=PostgresFreshness(config['DATA_COLLECTION_DATABASE_URL'])
auth=app.state.freshness_snapshots.auth
jobs=PostgresJobs(config['DATA_COLLECTION_DATABASE_URL'])
pub=jobs.publication(original.scope_id,UUID(params['publication']))
proof=DailyPublicationEvidence(auth,jobs,FileCollectionArchive(Path(config['COLLECTOR_ARCHIVE_ROOT']))).verify(pub.actor_id,pub.scope_id,pub.publication_id)
assert proof.key==original.key
assert original.evidence.published is not None
assert proof.published.complete_through<=original.evidence.published.complete_through
candidate=evaluate(proof.key,original.expectation,
    FreshnessEvidence(key=proof.key,source_health='unknown',collected=proof.collected,published=proof.published),
    original.checked_at+timedelta(microseconds=1))
# Replay a historical decision epoch in the dedicated verification scope, not
# today's provider health. No supplier call or artificial clock refresh occurs.
def count():
    with psycopg.connect(config['DATA_COLLECTION_DATABASE_URL']) as conn:
        return conn.execute('SELECT count(*) FROM qtrade_data.collection_freshness_snapshot '
            'WHERE scope_id=%s AND key_sha256=%s',
            (proof.key.scope_id,digest(proof.key.model_dump(mode='json')))).fetchone()[0]
before=count()
refresh=FreshnessRefresh(auth,repo)
first=refresh.commit(pub.actor_id,candidate)
assert (first.result.evidence.published.complete_through
    >= original.evidence.published.complete_through)
if first.state!='superseded':
    assert first.result.evidence.published==original.evidence.published
    assert first.result.evidence.collected==original.evidence.collected
second=refresh.commit(pub.actor_id,candidate)
assert second.state in {'replayed','superseded'} and second.result==first.result
after=count()
assert after==before+(1 if first.state=='recorded' else 0)
assert repo.latest(proof.key)==first.result
r=httpx.post('http://127.0.0.1:18100/v1/freshness/latest',headers={
    'Authorization':'Bearer '+config['COLLECTOR_SERVICE_TOKEN'],
    'X-Data-Actor':str(pub.actor_id),'X-Data-Scope':str(pub.scope_id)},
    json=proof.key.model_dump(mode='json'),timeout=30)
assert r.status_code==200 and FreshnessResult.model_validate_json(r.text)==first.result
print(json.dumps({'first':first.state,'second':second.state,'before':before,'after':after,
    'checked_at':first.result.checked_at.isoformat(),'status':first.result.status,
    'published_through':first.result.evidence.published.complete_through.isoformat(),
    'source_probe':False,'epoch':'historical original + 1 microsecond','public_query':True}))
"""
wrapped = (
    "PARAMS_JSON="
    + repr(json.dumps(params))
    + "\ntry:\n"
    + textwrap.indent(code, "    ")
    + """
except Exception as error:
    import json,traceback
    from pathlib import Path
    code=getattr(error,'code',None)
    allowed={'unavailable','unverified','forbidden','not_found','unsupported','conflict'}
    print(json.dumps({'error':type(error).__name__,'code':code if code in allowed else None,
        'frames':[{'file':Path(f.filename).name,'line':f.lineno}
            for f in traceback.extract_tb(error.__traceback__)[-3:]]}))
    raise SystemExit(1)
"""
)
# No bounded supplier subprocess is started here; only archive/DB/auth I/O.
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input=wrapped,
    text=True,
    capture_output=True,
)
if result.returncode:
    raise SystemExit("Freshness merge verification failed: " + result.stdout.strip())
values = json.loads(result.stdout)
path = ROOT / ".tools/verification-local/review/freshness-merge-results.json"
path.write_text(json.dumps(values, indent=2))
path.chmod(0o600)
print(
    "PASS real historical freshness merge: retained stronger checkpoints, "
    "replay/no duplicate and public read"
)
