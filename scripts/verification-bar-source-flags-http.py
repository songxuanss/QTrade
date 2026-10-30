"""Prove numeric SF zero in real HTTP bytes and its exact archived row binding."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
review = root / ".tools/verification-local/review"
replay = json.loads((review / "bar-flags-replay.json").read_text())
download = json.loads((review / "bar-flags-download.json").read_text())
original, current = replay["targets"][0], download["parts"][0]
value = {
    "scope": replay["scope_id"],
    "targets": [
        {
            "job": original["parent_job_id"],
            "part": original["parent_part_id"],
            "publication": original["original_publication_id"],
            "records": original["records"],
        },
        {
            "job": download["job_id"],
            "part": current["part_id"],
            "publication": current["publication_id"],
            "records": current["records"],
        },
    ],
}
code = """
import json
from uuid import UUID
from pathlib import Path
from decimal import Decimal
from base64 import b64decode
from qtrade_collector.config import configuration
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_postgres.collection_replay import PostgresCollectionReplays
from qtrade_data_contracts.collection import digest

config = configuration()
repo = PostgresCollectionReplays(config['DATA_COLLECTION_DATABASE_URL'])
archive = FileCollectionArchive(Path(config['COLLECTOR_ARCHIVE_ROOT']))
value = json.loads(INPUT_JSON)
scope = UUID(value['scope'])
output = []
for target in value['targets']:
    part = repo.part_for_job(scope, UUID(target['job']), UUID(target['part']))
    publication = repo.publication(scope, UUID(target['publication']))
    raw = archive.raw_records(publication)
    assert len(raw) == target["records"] > 0
    rows = []
    for receipt, _ in archive.source_row_proofs(part, publication.published_at):
        if receipt.kind != 'http_response':
            continue
        directory = archive.root / str(scope) / str(part.part_id) / 'source-captures'
        envelope = json.loads((directory / f'payload-{receipt.content_sha256}.json').read_bytes())
        if envelope.get('http_status') != 200 or not envelope.get('complete'):
            continue
        body = json.loads(b64decode(envelope['body_base64']), parse_float=Decimal)
        body = body.get('data') if isinstance(body, dict) else body
        if isinstance(body, list):
            rows.extend(row for row in body if isinstance(row, dict) and 'sf' in row)
    canonical = [json.loads(json.dumps(row, default=str)) for row in rows]
    assert {digest(row) for row in raw} <= {digest(row) for row in canonical}
    assert all(isinstance(row['sf'], Decimal) and row['sf'].is_zero() for row in rows)
    output.append(dict(records=len(raw), http_rows=len(rows),
        source_sf_json_numeric_zero=True, raw_bound_to_http=True))
print(json.dumps(dict(passed=True, results=output)))
"""
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input="INPUT_JSON=" + repr(json.dumps(value)) + "\n" + code,
    text=True,
    capture_output=True,
)
if result.returncode:
    raise SystemExit("Source HTTP type proof failed; private details withheld")
proof = json.loads(result.stdout)
output = review / "bar-flags-http-type-proof.json"
output.write_text(json.dumps(proof, indent=2))
output.chmod(0o600)
print(json.dumps(proof))
