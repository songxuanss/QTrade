"""Read the deployed watch and immutable revision history after actual UI/timer tests."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
result_path = root / ".tools/verification-local/review/freshness-watch-ui-results.json"
ui = json.loads(result_path.read_text())
assert ui["collector_restart"] is True, "Run the UI script with restart verification first"
selection = {
    "watch_id": ui["watch"]["watch_id"],
    "scope_id": ui["watch"]["scope_id"],
    "actor_id": ui["watch"]["actor_id"],
    "checked_at": ui["watch"]["last_checked_at"],
}
code = '''
import json,sys
from datetime import datetime
from uuid import UUID
from hashlib import sha256
from qtrade_collector.main import create_app
from qtrade_collector.config import configuration
from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.freshness_watch import FreshnessWatch
from qtrade_data_postgres.freshness import PostgresFreshness
from qtrade_data_postgres.migrate import DIRECTORY

def main():
    value=json.loads(sys.stdin.read());app=create_app();config=configuration()
    watch=app.state.freshness_watches.get(UUID(value['actor_id']),UUID(value['scope_id']),UUID(value['watch_id']))
    assert not watch.enabled and watch.paused_reason is None
    assert watch.last_error_code is None
    assert watch.last_checked_at==datetime.fromisoformat(value['checked_at'])
    repo=PostgresFreshness(config['DATA_COLLECTION_DATABASE_URL'])
    snapshot=repo.latest(watch.key)
    assert snapshot.status=='unknown' and snapshot.evidence.source_health=='unknown'
    assert snapshot.evidence.published.complete_through.isoformat()=='2025-12-31'
    assert snapshot.checked_at>=watch.last_checked_at
    with repo._connection() as conn:
        rows=conn.execute("""SELECT revision,action,payload_sha256,payload
            FROM qtrade_data.collection_freshness_watch_revision
            WHERE scope_id=%s AND watch_id=%s ORDER BY revision""",
            (watch.scope_id,watch.watch_id)).fetchall()
        migration=conn.execute("SELECT checksum FROM qtrade_data.schema_migration WHERE version=%s",
            ('0019_freshness_watch.sql',)).fetchone()
    assert rows and [r[0] for r in rows]==list(range(1,watch.revision+1))
    assert rows[0][1]=='created' and rows[-1][1]=='disabled'
    for revision,action,content,payload in rows:
        frozen=FreshnessWatch.model_validate_json(json.dumps(payload))
        assert frozen.revision==revision and digest(payload)==content
    expected=sha256((DIRECTORY/'0019_freshness_watch.sql').read_bytes()).hexdigest()
    assert migration and migration[0]==expected
    assert not repo.notices(watch.key), 'unknown history invented an incident'
    print(json.dumps({'migration_verified':True,'journal_revisions':len(rows),
        'watch_disabled':True,'real_snapshot':'unknown','archived_complete_through':'2025-12-31',
        'supplier_price_probe':False}))
try:main()
except Exception as error:
    print(json.dumps({'error_type':type(error).__name__}));sys.exit(1)
'''
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-c", code],
    input=json.dumps(selection),
    text=True,
    capture_output=True,
    check=False,
)
if result.returncode:
    raise SystemExit("Watch verification failed; private output withheld")
summary = json.loads(result.stdout.strip().splitlines()[-1])
summary["collector_image"] = subprocess.check_output(
    ["docker", "inspect", "--format", "{{.Image}}", "qtrade-collector-collector-1"], text=True
).strip()
summary["web_image"] = subprocess.check_output(
    ["docker", "inspect", "--format", "{{.Image}}", "qtrade-web-1"], text=True
).strip()
output = root / ".tools/verification-local/review/freshness-watch-persistence-results.json"
output.write_text(json.dumps(summary, indent=2) + "\n")
output.chmod(0o600)
print("PASS deployed watch, revision hashes, actual check and stopped state retained after restart")
