"""Bounded actual event API/Worker/provider/archive/publication check, no UI claim."""

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
        source="mairui.corporate.distributions",
        kind="corporate_action",
        instrument_ids=["600519.SH"],
        date_axis="event_date",
        start="1990-01-01",
        end="2027-01-01",
    )
    with httpx.Client(base_url="http://127.0.0.1:18100", headers=headers, timeout=30) as client:
        previous_files = list(
            (ROOT / ".tools/verification-local/review").glob("research-events-*.json")
        )
        previous = (
            json.loads(max(previous_files, key=lambda p: p.stat().st_mtime).read_text())["manifest"]
            if previous_files
            else None
        )
        if previous:
            assert (
                client.get("/v2/research/publications/" + previous["publication_id"]).json()
                == previous
            )
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
        assert manifest["artifact"]["record_count"] == 31
        query = dict(
            schema_version=2,
            actor_id=str(actor),
            scope_id=str(scope),
            publication_id=publication,
            content_version=manifest["content_version"],
            kind="corporate_action",
        )
        page = client.post("/v2/research/query", json=query)
        assert page.status_code == 200
        records = page.json()["records"]
        assert len(records) == 31
        assert all(r["action"] == "distribution" and r["basis_shares"] == "10" for r in records)
        assert (
            sum(
                bool(float(r["bonus_shares"] or 0) or float(r["transfer_shares"] or 0))
                for r in records
            )
            == 8
        )
        assert all(r["payment_date"] is None for r in records)
        assert all(r["knowledge"]["status"] == "unknown" for r in records)
        assert (
            client.post(
                "/v2/research/query", json=query | {"as_of": "2015-09-01T00:00:00Z"}
            ).status_code
            == 400
        )
        assert client.get("/v2/research/jobs/" + body["request_id"]).json() == state
        if previous:
            assert previous["publication_id"] != publication
            assert (
                client.get("/v2/research/publications/" + previous["publication_id"]).json()
                == previous
            )
            old_query = query | {
                "publication_id": previous["publication_id"],
                "content_version": previous["content_version"],
            }
            old_page = client.post("/v2/research/query", json=old_query)
            assert old_page.status_code == 200 and len(old_page.json()["records"]) == 31
        target = (
            ROOT / ".tools/verification-local/review" / f"research-events-{body['request_id']}.json"
        )
        target.write_text(
            json.dumps(
                dict(
                    evidence="live API/Worker/distributions publication; UI not tested",
                    request=body,
                    job=state,
                    manifest=manifest,
                ),
                indent=2,
            )
        )
        print("PASS live events: 31 distributions, 8 share events; PIT rejected; persisted")


if __name__ == "__main__":
    main()
