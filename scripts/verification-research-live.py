"""Bounded actual financial API/Worker/provider/archive/publication query check, no UI claim."""

import argparse
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--statement", choices=("balance", "income", "cashflow"), default="income")
    parser.add_argument("--year", type=int, choices=(2015, 2025), default=2015)
    args = parser.parse_args()
    if args.year == 2015 and args.statement != "income":
        parser.error("fixed 2015 acceptance is income only")
    expected = 6 if args.year == 2015 and args.statement == "income" else 4
    config = dotenv_values(ROOT / ".env.collector")
    report = json.loads(
        (ROOT / ".tools/verification-local/review/independent-collector-results.json").read_text()
    )
    info = json.loads(
        subprocess.check_output(["docker", "inspect", "qtrade-collector-collector-db-1"])
    )[0]
    address = info["NetworkSettings"]["Networks"]["qtrade-collector_default"]["IPAddress"]
    with psycopg.connect(
        make_conninfo(
            host=address,
            user="collector",
            dbname="collector",
            password=config["COLLECTOR_DB_PASSWORD"],
        )
    ) as conn:
        actor, scope = conn.execute(
            "SELECT actor_id,scope_id FROM qtrade_data.collection_job WHERE job_id=%s",
            (report["jobs"][0]["id"],),
        ).fetchone()
    headers = {
        "Authorization": "Bearer " + config["COLLECTOR_SERVICE_TOKEN"],
        "X-Data-Actor": str(actor),
        "X-Data-Scope": str(scope),
    }
    body = dict(
        schema_version=2,
        actor_id=str(actor),
        scope_id=str(scope),
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
        source="mairui.financial." + args.statement,
        kind="financial_statement",
        instrument_ids=["600519.SH"],
        date_axis="report_period",
        start=f"{args.year}-01-01",
        end=f"{args.year + 1}-01-01",
    )
    with httpx.Client(base_url="http://127.0.0.1:18100", headers=headers, timeout=30) as client:
        preview = client.post("/v2/research/preview", json=body)
        assert preview.status_code == 200
        assert client.get("/v2/research/jobs/" + body["request_id"]).status_code == 404
        submitted = client.post("/v2/research/jobs", json=body)
        assert submitted.status_code == 200
        assert client.post("/v2/research/jobs", json=body).status_code == 200
        deadline = time.monotonic() + 180
        while True:
            state = client.get("/v2/research/jobs/" + body["request_id"]).json()
            if state["state"] in {"completed", "failed", "no_observations"}:
                break
            assert time.monotonic() < deadline, "research task deadline"
            time.sleep(1)
        assert state["state"] == "completed", state
        publication = state["publication_id"]
        manifest = client.get("/v2/research/publications/" + publication).json()
        assert manifest["artifact"]["record_count"] == expected
        query = dict(
            schema_version=2,
            actor_id=str(actor),
            scope_id=str(scope),
            publication_id=publication,
            content_version=manifest["content_version"],
            kind="financial_statement",
        )
        page = client.post("/v2/research/query", json=query)
        assert page.status_code == 200
        records = page.json()["records"]
        assert len(records) == expected
        assert all(
            r["statement"] == args.statement and r["report_period"].startswith(str(args.year))
            for r in records
        )
        assert all(r["knowledge"]["status"] == "unknown" for r in records)
        assert (
            client.post(
                "/v2/research/query", json=query | {"as_of": "2015-09-01T00:00:00Z"}
            ).status_code
            == 400
        )
        assert client.get("/v2/research/jobs/" + body["request_id"]).json() == state
        target = (
            ROOT
            / ".tools/verification-local/review"
            / f"research-live-{args.statement}-{args.year}-{body['request_id']}.json"
        )
        target.write_text(
            json.dumps(
                dict(
                    evidence="live API/Worker/financial publication; UI not tested",
                    request=body,
                    job=state,
                    manifest=manifest,
                ),
                indent=2,
            )
        )
        print(
            f"PASS live {args.statement}/{args.year}: {expected} revisions; PIT rejected; persisted"
        )


if __name__ == "__main__":
    main()
