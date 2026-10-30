"""Verify the real long-range task's raw object checksums, records, and dates."""

import json
import runpy
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

from qtrade_contracts import DataBatch
from qtrade_object_store.s3 import S3ObjectStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec

state = json.loads(Path(".tools/sso-local/state.json").read_text())
address, values = runpy.run_path("scripts/verification-local.py")["container_environment"](
    "object-store"
)
objects = S3ObjectStore.connect(
    bucket="qtrade-verification",
    endpoint_url=f"http://{address}:9000",
    allow_http=True,
    access_key=values["MINIO_ROOT_USER"],
    secret_key=values["MINIO_ROOT_PASSWORD"],
)
with psycopg.connect(state["api_url"]) as conn:
    row = conn.execute(
        "SELECT task_id,request,status,collection_complete FROM ingestion_task "
        "WHERE request->>'start'='2026-09-14T01:30:00+00:00' "
        "AND request->>'end'='2026-09-22T07:00:00+00:00' "
        "ORDER BY request->>'created_at' DESC LIMIT 1"
    ).fetchone()
    assert row is not None and row[2:] == ("completed", True)
    task, request = row[:2]
    assert datetime.fromisoformat(request["end"]) - datetime.fromisoformat(
        request["start"]
    ) > timedelta(days=7)
    rows = conn.execute(
        "SELECT payload FROM raw_data_batch WHERE ingestion_task_id=%s", (task,)
    ).fetchall()
    assert rows
    dates = set()
    count = 0
    for (payload,) in rows:
        batch = DataBatch.model_validate_json(json.dumps(payload))
        content = objects.get(
            batch.workspace_id, batch.object_key, expected_sha256=batch.content_sha256
        )
        records = ParquetRawRecordCodec().decode(content, expected_count=batch.record_count)
        count += len(records)
        dates.update(json.loads(record.payload)["d"] for record in records)
    assert count > 0
    assert dates == {
        "2026-09-14",
        "2026-09-15",
        "2026-09-16",
        "2026-09-17",
        "2026-09-18",
        "2026-09-21",
        "2026-09-22",
    }
    print(
        f"PASS: completed range over seven days, {len(rows)} checksum-verified Parquet batches, "
        f"{count} real ticks, all seven requested trading dates"
    )
