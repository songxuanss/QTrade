"""Verify deployed v2 rejection surface and v1 persistence without inventing a source."""

import json
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from psycopg import connect
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = dotenv_values(ROOT / ".env.collector")
    report = json.loads(
        (ROOT / ".tools/verification-local/review/independent-collector-results.json").read_text()
    )
    token = config["COLLECTOR_SERVICE_TOKEN"]
    assert token
    # The saved manifest contains public identity, never credentials.
    info = json.loads(
        subprocess.check_output(["docker", "inspect", "qtrade-collector-collector-db-1"])
    )[0]
    address = info["NetworkSettings"]["Networks"]["qtrade-collector_default"]["IPAddress"]
    with connect(
        make_conninfo(
            host=address,
            user="collector",
            dbname="collector",
            password=config["COLLECTOR_DB_PASSWORD"],
        )
    ) as connection:
        actor, scope = connection.execute(
            "SELECT actor_id,scope_id FROM qtrade_data.collection_job WHERE job_id=%s",
            (report["jobs"][0]["id"],),
        ).fetchone()
    headers = {
        "Authorization": "Bearer " + token,
        "X-Data-Actor": str(actor),
        "X-Data-Scope": str(scope),
    }
    checks = []
    with httpx.Client(base_url="http://127.0.0.1:18100", timeout=30) as client:
        assert client.get("/v2/research/catalog").status_code == 401
        checks.append("missing service identity rejected")
        catalog = client.get("/v2/research/catalog", headers=headers)
        assert catalog.status_code == 200 and len(catalog.json()) == 4
        assert all(c["evidence"] == "sample" and not c["supports_as_of"] for c in catalog.json())
        checks.append("ordinary financial sample capabilities preserve PIT restriction")
        body = dict(
            schema_version=2,
            actor_id=str(actor),
            scope_id=str(scope),
            request_id=str(uuid4()),
            correlation_id=str(uuid4()),
            source="mairui.financial.income",
            kind="financial_statement",
            instrument_ids=["600519.SH"],
            date_axis="report_period",
            start="2015-01-01",
            end="2016-01-01",
        )
        response = client.post("/v2/research/preview", headers=headers, json=body)
        assert (
            response.status_code == 200
            and response.json()["request"]["request_id"] == body["request_id"]
        )
        checks.append("financial preview plans without creating jobs")
        assert (
            client.post(
                "/v2/research/preview", headers=headers, json=body | {"schema_version": 3}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/v2/research/preview", headers=headers, json=body | {"scope_id": str(uuid4())}
            ).status_code
            == 403
        )
        checks.append("unknown schema and cross-scope body rejected")
        query = dict(
            schema_version=2,
            actor_id=str(actor),
            scope_id=str(scope),
            publication_id=str(uuid4()),
            content_version="a" * 64,
            kind="financial_statement",
        )
        assert client.post("/v2/research/query", headers=headers, json=query).status_code == 404
        checks.append("nonexistent publication not returned as fixture data")
        assert (
            client.get(
                f"/v2/research/publications/{query['publication_id']}", headers=headers
            ).status_code
            == 404
        )
        checks.append("public manifest reader rejects unrecorded publications")
        job = client.get(f"/v1/jobs/{report['jobs'][0]['id']}", headers=headers)
        assert job.status_code == 200 and job.json()["state"] == "completed"
        assert all(p["publication"]["raw_record_count"] > 0 for p in job.json()["parts"])
        checks.append("previous v1 completed nonempty publications remain readable")
    target = ROOT / ".tools/verification-local/review/research-interface-results.json"
    target.write_text(
        json.dumps(
            {
                "evidence": "deployed HTTP rejection/compatibility; no supplier research readiness",
                "checks": checks,
            },
            indent=2,
        )
    )
    target.chmod(0o600)
    print(f"PASS deployed research HTTP surface: {len(checks)} checks")


if __name__ == "__main__":
    main()
