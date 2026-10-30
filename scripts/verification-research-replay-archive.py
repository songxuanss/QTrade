"""Read-only private proof for exact UI replay requests, not a download fixture."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
review = root / ".tools/verification-local/review"
value = json.loads((review / "research-replay-ui.json").read_text())
code = """
import json
from pathlib import Path
from uuid import UUID
from hashlib import sha256
import psycopg
from qtrade_collector.config import configuration
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_postgres.research_replay import PostgresResearchReplays
from qtrade_data_postgres.migrate import DIRECTORY
v=json.loads(INPUT_JSON);config=configuration()
repo=PostgresResearchReplays(config['DATA_COLLECTION_DATABASE_URL'])
archive=FileResearchArchive(Path(config['COLLECTOR_ARCHIVE_ROOT']))
scope=UUID(v['scope_id']);checks=[]
with psycopg.connect(config['DATA_COLLECTION_DATABASE_URL']) as conn:
 for path in DIRECTORY.glob('*.sql'):
  row=conn.execute('SELECT checksum FROM qtrade_data.schema_migration WHERE version=%s',
                   (path.name,)).fetchone()
  assert row and row[0]==sha256(path.read_bytes()).hexdigest()
 for t in v['targets']:
  original=repo.manifest(scope,UUID(t['publication_id']))
  intent=repo.replay(scope,UUID(t['replay_id']))
  fresh=repo.manifest(scope,UUID(t['replay_publication_id']))
  assert intent and original.content_version==t['content_version']
  assert original.artifact.records_sha256==t['records_sha256']
  assert intent.parent_plan==repo.plan(scope,UUID(t['request_id']))
  assert intent.target_plan==repo.plan(scope,UUID(t['replay_id']))
  assert original.artifact.raw_batches==intent.source_batches
  assert fresh.artifact.request_id==intent.command.request_id
  old_records,new_records=archive.records(original.artifact),archive.records(fresh.artifact)
  assert len(old_records)==len(new_records)==t['records']>0
  assert {r.collected_at for r in old_records}=={r.collected_at for r in new_records}
  assert all(r.knowledge.status=='unknown' and r.knowledge.known_at is None for r in new_records)
  assert fresh.quality.status in {'passed','warning'}
  assert len(fresh.artifact.raw_batches)==len(intent.source_batches)
  for old,new in zip(intent.source_batches,fresh.artifact.raw_batches,strict=True):
   assert old!=new and old.collected_at==new.collected_at
   assert archive.raw(old)==archive.raw(new)
   assert old.raw_content_sha256==new.raw_content_sha256
   assert repo.raw_manifest(scope,old.batch_id)==old
  count=conn.execute('SELECT count(*) FROM qtrade_data.research_response_receipt '
                     'WHERE scope_id=%s AND request_id=%s',
                     (scope,UUID(t['replay_id']))).fetchone()[0]
  assert count==0
  checks.append(dict(request_id=t['replay_id'],parent_request_id=t['request_id'],
    records=len(new_records),original_collected_at_preserved=True,knowledge_unknown=True,
    original_hashes_valid=True,raw_content_identical=True,child_http_receipts=0,
    quality_version=fresh.quality.rule_version))
print(json.dumps(dict(checks=checks,migration_checksums_valid=True)))
"""
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input="INPUT_JSON=" + repr(json.dumps(value)) + "\n" + code,
    text=True,
    capture_output=True,
)
if result.returncode:
    # Tracebacks may contain connection details. Keep private diagnostics out of
    # shared tool output; only the verified assertions below become evidence.
    raise SystemExit("Replay archive proof failed; private details withheld")
proof = json.loads(result.stdout)
p = review / "research-replay-archive-proof.json"
p.write_text(json.dumps(proof, indent=2))
p.chmod(0o600)
print("PASS exact UI replay RAW/standard/publication, old hashes, unknown PIT and migration ledger")
