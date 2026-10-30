"""Bind the exact compact UI task to persisted RAW, quality and publication."""

import json
import os
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
review = root / ".tools/verification-local/review"
namespace = os.environ.get("QTRADE_COLLECTION_REPORT", "collection-compact-ui")
assert re.fullmatch(r"[a-z0-9-]+", namespace)
run = json.loads((review / f"{namespace}.json").read_text())
scope = json.loads((review.parent / "budget-scope.json").read_text())["scope_id"]
assert run["live_completed"] and run["job_id"] and run["parts"]
inputs = {"scope": scope, "run": run, "namespace": namespace}
code = """
import json
from pathlib import Path
from uuid import UUID
from qtrade_collector.config import configuration
from qtrade_data_contracts.collection import digest
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_postgres.jobs import PostgresJobs
config=configuration();value=json.loads(INPUT_JSON)
repo=PostgresJobs(config['DATA_COLLECTION_DATABASE_URL'])
archive=FileCollectionArchive(Path(config['COLLECTOR_ARCHIVE_ROOT']))
scope,job=UUID(value['scope']),UUID(value['run']['job_id'])
results=[]
for item in value['run']['parts']:
 part=repo.part_for_job(scope,job,UUID(item['part_id']))
 pub=repo.publication(scope,UUID(item['publication_id']))
 assert part.state=='completed' and pub.instrument_id=='601288.SH' and pub.granularity=='1d'
 raw,records=archive.raw_records(pub),archive.records(pub)
 assert len(raw)==len(records)==5 and all(r.instrument_id=='601288.SH' for r in records)
 if value['namespace']=='bar-flags-download':
  assert pub.mapping_version=='mairui.history-bars.v3'
  assert all(r.quality=='valid' for r in records)
  assert pub.evidence['source_flag_unknown_count']==0
 decisions=archive.qualities(part)
 assert any(q.status in {'passed','warning'} and q.records_sha256==pub.records_sha256
            and q.record_count==5 for q in decisions)
 proofs=archive.source_row_proofs(part,pub.published_at)
 kinds={receipt.kind for receipt,_ in proofs}
 history=set().union(*(rows for receipt,rows in proofs
                      if receipt.kind in {'history','cached_history'}))
 assert {digest(row) for row in raw} <= history
 prepared=archive.normalization_inputs(part)
 assert len(prepared)==1 and prepared[0].raw_capture_ids
 results.append(dict(part_id=item['part_id'],publication_id=item['publication_id'],
  raw_records=len(raw),records=len(records),hashes_valid=True,source_history_bound=True,
  source_receipt_kinds=sorted(kinds),
  quality_versions=sorted({q.rule_version for q in decisions})))
print(json.dumps(dict(job_id=str(job),parts=results)))
"""
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input="INPUT_JSON=" + repr(json.dumps(inputs)) + "\n" + code,
    text=True,
    capture_output=True,
)
if result.returncode:
    raise SystemExit("Compact UI archive proof failed; private details withheld")
proof = json.loads(result.stdout)
path = review / f"{namespace.removesuffix('-ui')}-archive-proof.json"
path.write_text(json.dumps(proof, indent=2))
path.chmod(0o600)
print(json.dumps({"passed": True, "parts": proof["parts"]}))
