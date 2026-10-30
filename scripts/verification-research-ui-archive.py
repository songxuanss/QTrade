"""Recheck the exact UI-created research publications against deployed storage.

No new tasks or source calls. Credentials remain inside the collector process;
only public run/publication identities enter the subprocess input.
"""

import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
targets = []
suffix = "-confirmed" if "--confirmed" in sys.argv else ""
for name in ("financial", "distribution"):
    evidence = json.loads(
        (ROOT / f".tools/verification-local/review/{name}-ui-results{suffix}.json").read_text()
    )["download"]
    # Validate identities before passing evidence to the deployed verifier.
    UUID(evidence["request_id"])
    UUID(evidence["publication_id"])
    targets.append(evidence)

code = """
import json, os
from pathlib import Path
from uuid import UUID
import psycopg
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_postgres.research_jobs import PostgresResearchJobs

targets = json.loads(TARGETS_JSON)
database = os.environ['DATA_COLLECTION_DATABASE_URL']
repo = PostgresResearchJobs(database)
archive = FileResearchArchive(Path(os.environ.get('COLLECTOR_ARCHIVE_ROOT', '/data/archives')))
with psycopg.connect(database) as conn:
    for target in targets:
        request, publication = UUID(target['request_id']), UUID(target['publication_id'])
        row = conn.execute('SELECT scope_id FROM qtrade_data.research_run WHERE request_id=%s',
                           (request,)).fetchall()
        assert len(row) == 1
        scope = row[0][0]
        job = repo.job(scope, request)
        assert job['state'] == 'completed' and job['publication_id'] == publication
        manifest = repo.manifest(scope, publication)
        assert manifest.artifact.request_id == request
        assert manifest.content_version == target['content_version']
        assert manifest.artifact.records_sha256 == target['records_sha256']
        records = archive.records(manifest.artifact)
        assert len(records) == target['records'] > 0
        assert len(manifest.artifact.raw_batches) == target['raw_batches'] > 0
        for batch in manifest.artifact.raw_batches:
            raw = archive.raw(batch)
            assert len(raw) == batch.raw_record_count > 0
print('PASS 2 exact UI-created research versions: persisted nonempty raw/standard and hashes')
"""
# repr produces a Python string literal, not shell text. No shell execution or
# credential configuration is involved; the container checks its own volumes.
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input="TARGETS_JSON = " + repr(json.dumps(targets)) + "\n" + code,
    text=True,
    capture_output=True,
)
if result.returncode:
    # Provider/DB exceptions can contain private URLs; never forward stderr.
    raise SystemExit("Research UI archive verification failed; inspect privately")
print(result.stdout.strip())
