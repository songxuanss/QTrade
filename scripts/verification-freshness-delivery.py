"""Controlled incidents through real HTTP/Worker/alerts/inbox, never vendor outage proof."""

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4, uuid5

import psycopg

ROOT = Path(__file__).resolve().parents[1]
folder = ROOT / ".tools/verification-local/review"
plan = folder / "freshness-delivery-fixture.json"
if not plan.exists():
    baseline = json.loads((folder / "collection-retest-ui.json").read_text())
    selection = {
        "scope_id": baseline["scope_id"],
        "publication_id": baseline["runs"][0]["parts"][0]["publication_id"],
        "run_id": str(uuid4()),
    }
    plan.write_text(json.dumps(selection))
    plan.chmod(0o600)
selection = json.loads(plan.read_text())
code = """
import json,sys
from datetime import UTC,datetime,timedelta
from uuid import UUID
from qtrade_collector.main import create_app
from qtrade_collector.config import configuration
from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.freshness import (
    FreshnessKey,FreshnessExpectation,FreshnessEvidence,FreshnessCheckpoint)
from qtrade_data_core.freshness import evaluate
from qtrade_data_postgres.jobs import PostgresJobs
from qtrade_data_postgres.freshness import PostgresFreshness

def main():
    selection=json.loads(sys.stdin.read());config=configuration();app=create_app()
    scope=UUID(selection['scope_id'])
    pub=PostgresJobs(config['DATA_COLLECTION_DATABASE_URL']).publication(scope,UUID(selection['publication_id']))
    actor=pub.actor_id
    key=FreshnessKey(scope_id=scope,source='verification.freshness.delivery',
        instrument_id='601288.SH',dataset_kind='bars',period='1d',adjustment='unadjusted',
        date_axis='observation_date',mapping_version='fixture.'+selection['run_id'])
    repo=PostgresFreshness(config['DATA_COLLECTION_DATABASE_URL'])
    service=app.state.freshness_check.refresh
    existing=repo.notices(key)
    if not existing:
        now=datetime.now(UTC)
        policy=FreshnessExpectation(complete_through=now.date()-timedelta(days=1),
            available_at=now-timedelta(hours=1),timezone='Asia/Shanghai',
            rule_version='fixture.delivery.v1',basis='Controlled delivery fixture; not vendor data')
        bad=FreshnessEvidence(key=key,source_health='unavailable')
        service.commit(actor,evaluate(key,policy,bad,now))
        service.commit(actor,evaluate(key,policy,bad,datetime.now(UTC)))
        existing=repo.notices(key)
    if len(existing)==1:
        policy=existing[0].result.expectation;now=datetime.now(UTC)
        point=FreshnessCheckpoint(complete_through=policy.complete_through,observed_at=now,
            evidence_sha256=digest({'fixture':selection['run_id']}))
        state=FreshnessEvidence(key=key,source_health='available',source=point,collected=point,published=point)
        service.commit(actor,evaluate(key,policy,state,now))
    notices=repo.notices(key)
    assert len(notices)==2 and [n.kind for n in notices]==['recovered','opened']
    with repo._connection() as conn:
        migrations=conn.execute(
            "SELECT version FROM qtrade_data.schema_migration WHERE version=%s",
            ('0018_freshness_delivery.sql',)).fetchall()
    assert migrations
    print(json.dumps({'scope_id':str(scope),'actor_id':str(actor),'controlled_producer':True,
        'notices':[{'notice_id':str(n.notice_id),'kind':n.kind,
            'episode_id':str(n.episode_id)} for n in reversed(notices)]}))
try:main()
except Exception as error:
    print(json.dumps({'error_type':type(error).__name__}));sys.exit(1)
"""
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-c", code],
    input=json.dumps(selection),
    text=True,
    capture_output=True,
    check=False,
)
if result.returncode:
    raise SystemExit("Controlled producer failed; private process output withheld")
summary = json.loads(result.stdout.strip().splitlines()[-1])
state = json.loads((ROOT / ".tools/sso-local/state.json").read_text())
# Only inspect the exact controlled identities. Do not clear other alerts or queues.
expected = ["collector-freshness:" + n["notice_id"] for n in summary["notices"]]
deadline = time.monotonic() + 50
while True:
    with psycopg.connect(state["api_url"]) as conn:
        rows = conn.execute(
            """SELECT event_id,source_event_id,event_type FROM qtrade.alert_event
            WHERE workspace_id=%s AND source_event_id=ANY(%s)""",
            (summary["scope_id"], expected),
        ).fetchall()
        notifications = [uuid5(row[0], f"in_app:{summary['scope_id']}") for row in rows]
        delivered = conn.execute(
            """SELECT count(*) FROM qtrade.notification_delivery d
            JOIN qtrade.in_app_notification n USING(workspace_id,notification_id)
            WHERE d.workspace_id=%s AND d.notification_id=ANY(%s) AND d.status='delivered'""",
            (summary["scope_id"], notifications),
        ).fetchone()[0]
    if len(rows) == 2 and delivered == 2:
        break
    if time.monotonic() >= deadline:
        raise SystemExit(f"Delivery incomplete: events={len(rows)}, inbox delivered={delivered}")
    time.sleep(0.5)
summary["events"] = [{"event_id": str(r[0]), "source_event_id": r[1], "type": r[2]} for r in rows]
ack_code = '''
import json,sys,psycopg
from qtrade_collector.config import configuration
value=json.loads(sys.stdin.read())
with psycopg.connect(configuration()['DATA_COLLECTION_DATABASE_URL']) as conn:
    count=conn.execute("""SELECT count(*) FROM qtrade_data.collection_freshness_delivery
        WHERE scope_id=%s AND notice_id::text=ANY(%s)
        AND consumer_id='qtrade-alerts' AND state='delivered' AND delivered_at IS NOT NULL""",
        (value['scope_id'],[n['notice_id'] for n in value['notices']])).fetchone()[0]
assert count==2
print(json.dumps({'acknowledged':count}))
'''
ack_result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-c", ack_code],
    input=json.dumps(summary),
    text=True,
    capture_output=True,
    check=False,
)
if ack_result.returncode:
    raise SystemExit("Collector acknowledgements incomplete; private output withheld")
summary["collector_acknowledged"] = json.loads(ack_result.stdout)["acknowledged"]
summary["inbox_delivered"] = delivered
summary["supplier_outage_verified"] = False
output = folder / "freshness-delivery-results.json"
output.write_text(json.dumps(summary, indent=2) + "\n")
output.chmod(0o600)
print(
    "PASS controlled incidents: collector HTTP/Worker/alerts and two delivered inbox notifications"
)
