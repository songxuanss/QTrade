"""Read-only binding of the exact new UI annual job to private source/RAW/quality."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
review = root / ".tools/verification-local/review"
run = json.loads((review / "collection-layout-ui.json").read_text())
assert run["completed_at"] and len(run["runs"]) == 1
assert run["runs"][0]["state"] == "completed" and run["runs"][0]["nonempty"]
inputs = {"scope": run["scope_id"], "run": run["runs"][0]}
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
 assert len(raw)==len(records)==243 and all(r.instrument_id=='601288.SH' for r in records)
 decisions=archive.qualities(part)
 assert any(q.status in {'passed','warning'} and q.records_sha256==pub.records_sha256
            and q.record_count==243 for q in decisions)
 proofs=archive.source_row_proofs(part,pub.published_at)
 history=set().union(*(rows for receipt,rows in proofs if receipt.kind=='history'))
 assert {digest(row) for row in raw} <= history
 assert any(r.kind=='http_response' for r,_ in proofs)
 results.append(dict(part_id=item['part_id'],publication_id=item['publication_id'],
  raw_records=len(raw),records=len(records),hashes_valid=True,source_history_bound=True,
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
    raise SystemExit("UI collection archive proof failed; private details withheld")
proof = json.loads(result.stdout)
path = review / "data-layout-archive-proof.json"
path.write_text(json.dumps(proof, indent=2))
path.chmod(0o600)
print("PASS exact UI annual job: nonempty RAW/standard, private HTTP/history and quality binding")
