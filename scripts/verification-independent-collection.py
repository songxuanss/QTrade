"""Verify deployed collector publication persistence through its public HTTP seam.

Consumes only private local configuration; never prints connection strings or credentials.
Run after apps/web/tests/verification-independent-collector.mjs.
"""

import json
import os
import subprocess
from pathlib import Path
from uuid import UUID

import httpx
import psycopg
from psycopg.conninfo import make_conninfo

from qtrade_data_contracts.collection import CollectionPublication, CollectionRecordPage, digest
from qtrade_data_http.client import CollectorClient

ROOT = Path(__file__).resolve().parents[1]


def verify() -> None:
    config = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.collector").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    bar_period = os.environ.get("QTRADE_VERIFICATION_BAR_PERIOD", "5m")
    assert bar_period in {"1m", "5m"}
    suffix = "-1m" if bar_period == "1m" else ""
    if os.environ.get("QTRADE_VERIFY_CONFIRMED") == "true":
        suffix += "-confirmed"
    results = json.loads(
        (
            ROOT / f".tools/verification-local/review/independent-collector-results{suffix}.json"
        ).read_text()
    )
    info = json.loads(
        subprocess.check_output(["docker", "inspect", "qtrade-collector-collector-db-1"])
    )[0]
    address = info["NetworkSettings"]["Networks"]["qtrade-collector_default"]["IPAddress"]
    conninfo = make_conninfo(
        host=address, user="collector", password=config["COLLECTOR_DB_PASSWORD"], dbname="collector"
    )
    client = CollectorClient(
        "http://127.0.0.1:18100", config["COLLECTOR_SERVICE_TOKEN"], allow_http=True
    )
    checked = []
    # Additional completed runs (such as annual daily collection) receive the
    # same public page/digest and consumer checks, not only receipt inspection.
    jobs = list(results["jobs"])
    existing = {job["id"] for job in jobs}
    for value in os.environ.get("QTRADE_VERIFY_EXTRA_JOB_IDS", "").split(","):
        if value and str(UUID(value)) not in existing:
            jobs.append({"id": str(UUID(value))})
            existing.add(str(UUID(value)))
    with psycopg.connect(conninfo) as conn:
        for job in jobs:
            row = conn.execute(
                "SELECT scope_id,actor_id FROM qtrade_data.collection_job WHERE job_id=%s",
                (UUID(job["id"]),),
            ).fetchone()
            assert row is not None
            scope, actor = row
            stored = client.request("GET", f"/v1/jobs/{job['id']}", actor_id=actor, scope_id=scope)
            assert stored["state"] == "completed"
            for part in stored["parts"]:
                publication = CollectionPublication.model_validate_json(
                    json.dumps(part["publication"])
                )
                offset, records = 0, []
                while True:
                    value = client.request(
                        "GET",
                        f"/v1/publications/{publication.publication_id}/records",
                        actor_id=actor,
                        scope_id=scope,
                        params={"offset": offset, "limit": 1000},
                    )
                    page = CollectionRecordPage.model_validate_json(json.dumps(value))
                    assert page.scope_id == scope and page.offset == offset
                    records.extend(item.model_dump(mode="json") for item in page.records)
                    if page.next_offset is None:
                        break
                    offset = page.next_offset
                assert len(records) == publication.record_count > 0
                assert digest(records) == publication.records_sha256
                # The server's archive reader independently checks raw Parquet byte SHA and count.
                checked.append(
                    {
                        "id": str(publication.publication_id),
                        "scope_id": str(scope),
                        "dataset_kind": publication.dataset_kind,
                        "job_id": job["id"],
                        "source": publication.source,
                        "mapping_version": publication.mapping_version,
                        "raw_records": publication.raw_record_count,
                        "records": publication.record_count,
                        "records_sha256": publication.records_sha256,
                        "raw_sha256": publication.raw_sha256,
                    }
                )
    state = json.loads((ROOT / ".tools/sso-local/state.json").read_text())
    market_id = subprocess.check_output(
        ["docker", "compose", "ps", "-q", "market-db"], text=True
    ).strip()
    market_info = json.loads(subprocess.check_output(["docker", "inspect", market_id]))[0]
    market_env = dict(item.split("=", 1) for item in market_info["Config"]["Env"])
    market_ip = next(iter(market_info["NetworkSettings"]["Networks"].values()))["IPAddress"]
    market_url = make_conninfo(
        host=market_ip,
        user=market_env["POSTGRES_USER"],
        password=market_env["POSTGRES_PASSWORD"],
        dbname=market_env["POSTGRES_DB"],
    )
    imported = []
    with psycopg.connect(state["api_url"]) as business, psycopg.connect(market_url) as market:
        for pub in checked:
            if pub["dataset_kind"] != "bars":
                continue
            row = business.execute(
                "SELECT workspace_id,state FROM collector_import WHERE publication_id=%s",
                (UUID(pub["id"]),),
            ).fetchone()
            assert row is not None and row[1] == "visible"
            assert str(row[0]) == pub["scope_id"]
            count = market.execute(
                "SELECT count(*) FROM qtrade_market.market_bar "
                "WHERE workspace_id=%s AND dataset_id=%s",
                (row[0], UUID(pub["id"])),
            ).fetchone()[0]
            assert count == pub["records"] > 0
            imported.append({"id": pub["id"], "bars": count, "state": "visible"})
    logs = subprocess.check_output(
        ["docker", "logs", "qtrade-collector-collector-1"], stderr=subprocess.STDOUT
    ).decode()
    secrets = [
        config.get(name)
        for name in ("MAIRUI_LICENCE", "COLLECTOR_SERVICE_TOKEN", "COLLECTOR_DB_PASSWORD")
    ]
    assert not any(value and value in logs for value in secrets)
    assert httpx.get("http://127.0.0.1:18100/ready").json() == {"ready": True}
    report = {
        "market_imports": imported,
        "publications": checked,
        "secret_scan": "passed",
        "http_after_restart": "passed",
    }
    if os.environ.get("QTRADE_VERIFY_RAW_CAPTURES") == "true":
        capture_targets = list(checked)
        with psycopg.connect(conninfo) as conn:
            for value in os.environ.get("QTRADE_VERIFY_CAPTURE_JOB_IDS", "").split(","):
                if not value:
                    continue
                job_id = UUID(value)
                row = conn.execute(
                    "SELECT scope_id,actor_id FROM qtrade_data.collection_job WHERE job_id=%s",
                    (job_id,),
                ).fetchone()
                assert row is not None
                scope, actor = row
                stored = client.request("GET", f"/v1/jobs/{job_id}", actor_id=actor, scope_id=scope)
                assert stored["state"] in {"completed", "no_data"}
                rows = conn.execute(
                    """SELECT p.part_id,p.payload,p.state,p.publication
                    FROM qtrade_data.collection_part p
                    JOIN qtrade_data.collection_job_part l USING(scope_id,part_id)
                    WHERE l.scope_id=%s AND l.job_id=%s""",
                    (scope, job_id),
                ).fetchall()
                for part_id, payload, state, publication in rows:
                    capture_targets.append(
                        {
                            "id": str(part_id),
                            "scope_id": str(scope),
                            "source": stored["request"]["source"],
                            "mapping_version": payload["mapping_version"],
                            "records": publication["record_count"] if publication else 0,
                            "records_sha256": publication["records_sha256"]
                            if publication
                            else None,
                            "no_publication": state == "no_data",
                            "expect_empty_factor": state == "no_data"
                            and stored["request"]["dataset_kind"] == "factors",
                        }
                    )
        for target in capture_targets:
            target["verify_quality"] = os.environ.get("QTRADE_VERIFY_COLLECTION_QUALITY") == "true"
        code = """
import json,sys
from pathlib import Path
from hashlib import sha256
from qtrade_data_contracts.collection import CollectionRawCapture
from qtrade_data_contracts.collection_quality import CollectionQualityResult
results=[]
for publication in json.load(sys.stdin):
 directory=Path('/data/archives')/publication['scope_id']/publication['id']/'source-captures'
 receipts=[CollectionRawCapture.model_validate_json(p.read_bytes())
           for p in directory.glob('receipt-*.json')]
 assert receipts, 'source captures missing'
 assert any(r.kind in {'history','cached_history','factor_history'} for r in receipts)
 if publication.get('no_publication'):
  assert not (directory.parent/'manifest.json').exists()
 if publication.get('expect_empty_factor'):
  assert any(r.kind=='factor_history' and r.response_rows==0 for r in receipts)
 for receipt in receipts:
  assert str(receipt.scope_id)==publication['scope_id'] and str(receipt.part_id)==publication['id']
  assert receipt.source==publication['source']
  assert receipt.mapping_version==publication['mapping_version']
  body=(directory/f'payload-{receipt.content_sha256}.json').read_bytes()
  assert sha256(body).hexdigest()==receipt.content_sha256
  json.loads(body)
  if receipt.kind=='http_response':
   from base64 import b64decode
   envelope=json.loads(body)
   assert set(envelope)=={'http_status','operation','body_base64','complete'}
   assert envelope['complete'] is True and 100<=envelope['http_status']<=599
   assert envelope['operation'] in {'bars.history','factors.history','metadata.calendar'}
   b64decode(envelope['body_base64'],validate=True)
 quality_summary=None
 if publication.get('verify_quality'):
  decisions=[CollectionQualityResult.model_validate_json(p.read_bytes())
             for p in directory.parent.glob('quality-*.json')]
  assert decisions, 'quality decision missing'
  decision=max(decisions,key=lambda value:value.checked_at)
  assert str(decision.scope_id)==publication['scope_id']
  assert str(decision.part_id)==publication['id']
  assert decision.record_count==publication['records']
  if publication.get('no_publication'):
   assert decision.status=='no_observations' and decision.record_count==0
  else:
   assert decision.status in {'passed','warning'}
   assert decision.records_sha256==publication['records_sha256']
  quality_summary={'rule_version':decision.rule_version,'status':decision.status,
                   'issues':[issue.code for issue in decision.issues]}
 results.append({'part_id':publication['id'],'receipts':len(receipts),
                 'published':not publication.get('no_publication',False),
                 'quality':quality_summary,
                 'kinds':sorted({r.kind for r in receipts})})
print(json.dumps(results))
"""
        result = subprocess.run(
            ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-c", code],
            input=json.dumps(capture_targets),
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, "private source capture verification failed"
        report["source_captures"] = json.loads(result.stdout)
    (
        ROOT / f".tools/verification-local/review/independent-collector-persistence{suffix}.json"
    ).write_text(json.dumps(report, indent=2))
    print(f"Verified {len(checked)} nonempty publications: raw/archive and paged records checksums")


if __name__ == "__main__":
    try:
        verify()
    except Exception:
        # Test runners must not expand private connection state into exception reports.
        raise SystemExit(
            "Independent collector persistence verification failed (private details omitted)"
        ) from None
