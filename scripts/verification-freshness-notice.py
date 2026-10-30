"""Read actual deployed outbox state; no supplier calls or notification fixtures."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
baseline = json.loads(
    (root / ".tools/verification-local/review/collection-retest-ui.json").read_text()
)
selection = {
    "scope_id": baseline["scope_id"],
    "publications": [
        next(part for part in run["parts"] if part["instrument_id"] == "601288.SH")[
            "publication_id"
        ]
        for run in baseline["runs"]
    ],
}
code = """
import json,sys
from uuid import UUID
from hashlib import sha256
from qtrade_collector.main import create_app
from qtrade_collector.config import configuration
from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.freshness import FreshnessKey
from qtrade_data_postgres.freshness import PostgresFreshness
from qtrade_data_postgres.jobs import PostgresJobs
from qtrade_data_postgres.migrate import DIRECTORY

def main():
    selection=json.loads(sys.stdin.read())
    config=configuration()
    app=create_app()
    repo=PostgresFreshness(config['DATA_COLLECTION_DATABASE_URL'])
    jobs=PostgresJobs(config['DATA_COLLECTION_DATABASE_URL'])
    scope=UUID(selection['scope_id'])
    seen=set()
    for publication_id in selection['publications']:
        pub=jobs.publication(scope,UUID(publication_id))
        assert pub.dataset_kind=='bars' and pub.granularity=='1d'
        key=FreshnessKey(scope_id=scope,source=pub.source,instrument_id=pub.instrument_id,
            dataset_kind='bars',period='1d',adjustment='unadjusted',
            date_axis='observation_date',mapping_version=pub.mapping_version)
        value=app.state.freshness_snapshots.latest(pub.actor_id,key)
        assert value.status=='unknown' and value.evidence.source_health=='unknown'
        assert value.evidence.published.complete_through.isoformat()=='2025-12-31'
        assert not repo.notices(key), 'unknown history invented an incident'
        seen.add(digest(key.model_dump(mode='json')))
    with repo._connection() as conn:
        row=conn.execute("SELECT checksum FROM qtrade_data.schema_migration WHERE version=%s",
            ('0017_freshness_notice.sql',)).fetchone()
    assert row and row[0]==sha256((DIRECTORY/'0017_freshness_notice.sql').read_bytes()).hexdigest()
    print(json.dumps({'migration_checksum_verified':True,
        'publications_checked':len(selection['publications']), 'logical_keys':len(seen),
        'unknown_history_invented_no_incident':True,'supplier_calls':False}))
try:
    main()
except Exception as error:
    print(json.dumps({'error_type':type(error).__name__}))
    sys.exit(1)
"""
# Keep source on the command line and selections on stdin. No credentials are
# copied out of the container's existing private configuration.
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-c", code],
    input=json.dumps(selection),
    text=True,
    capture_output=True,
    check=False,
)
if result.returncode:
    raise SystemExit("Deployed freshness notice verification failed; no private output emitted")
summary = json.loads(result.stdout.strip().splitlines()[-1])
summary["collector_image"] = subprocess.check_output(
    ["docker", "inspect", "--format", "{{.Image}}", "qtrade-collector-collector-1"], text=True
).strip()
output = root / ".tools/verification-local/review/freshness-notice-results.json"
output.write_text(json.dumps(summary, indent=2) + "\n")
output.chmod(0o600)
print("PASS deployed migration, real unknown snapshots and no invented incidents")
