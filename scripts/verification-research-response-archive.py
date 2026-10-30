"""Read-only deployed receipt/body/raw/publication proof, without printing bodies."""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--partial", action="store_true", help="Verify controlled incomplete replies")
partial = parser.parse_args().partial
suffix = "-partial" if partial else ""
review = ROOT / ".tools/verification-local/review"
targets = []
for kind in ("financial", "distribution"):
    value = json.loads((review / f"{kind}-ui-results.json").read_text())["download"]
    targets.append(value | {"fixture": False})
targets.extend(
    json.loads(
        (ROOT / f".tools/verification-local/research-response{suffix}-fixtures.json").read_text()
    )
)
code = """
import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID
import psycopg
from qtrade_collector.config import configuration
from qtrade_data_contracts.research_acquisition import ResearchResponseReceipt
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_postgres.research_jobs import PostgresResearchJobs
from qtrade_data_postgres.research_acquisition import PostgresResearchResponses
from qtrade_data_postgres.migrate import DIRECTORY
from qtrade_data_mairui.collection import MaiRuiCollection
config=configuration();targets=json.loads(TARGETS_JSON)
repo=PostgresResearchJobs(config['DATA_COLLECTION_DATABASE_URL'])
store=PostgresResearchResponses(config['DATA_COLLECTION_DATABASE_URL'])
archive=FileResearchArchive(Path(config['COLLECTOR_ARCHIVE_ROOT']))
results=[]
for target in targets:
    scope,request=UUID(target['scope_id']),UUID(target['request_id'])
    plan=repo.plan(scope,request);job=repo.job(scope,request)
    page=store.page(plan,100,None)
    assert page.receipts and page.next_id is None
    for receipt in page.receipts:
        body=archive.response_body(receipt)
        assert len(body)==receipt.body_bytes
        assert sha256(body).hexdigest()==receipt.body_sha256
    if target['fixture']:
        assert job['state']=='failed' and job['publication_id'] is None
        assert len(page.receipts)==target['receipt_count']
        assert all(r.http_status==target['receipt']['http_status'] for r in page.receipts)
        assert all(r.complete==target['receipt']['complete'] for r in page.receipts)
        assert repo.stage(scope,request)=='registered'
        results.append(dict(request_id=str(request),fixture=True,
            receipts=len(page.receipts),body_hashes_valid=True,publication=False))
        continue
    assert job['state']=='completed' and str(job['publication_id'])==target['publication_id']
    manifest=repo.manifest(scope,UUID(target['publication_id']))
    assert manifest.content_version==target['content_version']
    assert archive.records(manifest.artifact)
    covered=set()
    for receipt in page.receipts:
        assert receipt.http_status==200 and receipt.complete
        assert archive.response_body(receipt)
        source=MaiRuiCollection._rows(json.loads(archive.response_body(receipt),parse_float=Decimal))
        rows=tuple({k:format(v,'f') if isinstance(v,Decimal) else v for k,v in row.items()}
                   | {'_instrument_id':receipt.instrument_id} for row in source)
        matches=[b for b in manifest.artifact.raw_batches if
            (b.instrument_id,b.start,b.end,b.cursor)==
            (receipt.instrument_id,receipt.start,receipt.end,receipt.cursor)]
        assert len(matches)==1 and archive.raw(matches[0])==rows
        covered.add(matches[0].batch_id)
    assert covered=={b.batch_id for b in manifest.artifact.raw_batches}
    results.append(dict(request_id=str(request),fixture=False,receipts=len(page.receipts),
        body_hashes_valid=True,raw_link_verified=True,records=manifest.artifact.record_count,
        publication_id=target['publication_id'],content_version=manifest.content_version))
version='0020_research_responses.sql'
with psycopg.connect(config['DATA_COLLECTION_DATABASE_URL']) as conn:
    (checksum,)=conn.execute(
        'SELECT checksum FROM qtrade_data.schema_migration WHERE version=%s',(version,)
    ).fetchone()
assert checksum==sha256((DIRECTORY/version).read_bytes()).hexdigest()
print(json.dumps(dict(migration=version,checksum_valid=True,results=results)))
"""
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input="TARGETS_JSON=" + repr(json.dumps(targets)) + "\n" + code,
    text=True,
    capture_output=True,
)
if result.returncode:
    raise SystemExit("Research response archive proof failed; private output withheld")
proof = json.loads(result.stdout)
path = review / f"research-response{suffix}-archive-results.json"
path.write_text(json.dumps(proof, indent=2))
path.chmod(0o600)
print("PASS deployed receipt bytes/SHA, parsed raw binding and immutable publication proof")
