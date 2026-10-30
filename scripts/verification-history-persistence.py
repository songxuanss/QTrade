"""Verify actual historical downloads against raw objects, reports and Timescale."""

import json
import runpy
from pathlib import Path
from urllib.parse import quote

import psycopg

from qtrade_contracts import DataBatch, MarketDataset
from qtrade_object_store.s3 import S3ObjectStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec

state = json.loads(Path(".tools/sso-local/state.json").read_text())
container = runpy.run_path("scripts/verification-local.py")["container_environment"]
address, values = container("object-store")
objects = S3ObjectStore.connect(
    bucket="qtrade-verification",
    endpoint_url=f"http://{address}:9000",
    allow_http=True,
    access_key=values["MINIO_ROOT_USER"],
    secret_key=values["MINIO_ROOT_PASSWORD"],
)
market_address, market = container("market-db")
market_url = (
    f"postgresql://{quote(market['POSTGRES_USER'], safe='')}:"
    f"{quote(market['POSTGRES_PASSWORD'], safe='')}@{market_address}:5432/"
    f"{market['POSTGRES_DB']}"
)
with psycopg.connect(state["api_url"]) as conn, psycopg.connect(market_url) as market_conn:
    for period in ("1d", "5m"):
        result = json.loads(
            Path(f".tools/verification-local/review/history-{period}-results.json").read_text()
        )
        task = conn.execute(
            "SELECT request,status,collection_complete FROM ingestion_task WHERE task_id=%s",
            (result["taskId"],),
        ).fetchone()
        assert task is not None and task[1:] == ("completed", True)
        assert task[0]["source"] == "mairui.a-share.bar" and task[0]["granularity"] == period
        row = conn.execute(
            "SELECT payload,quality_report_sha256 FROM market_dataset WHERE dataset_id=%s",
            (result["datasetId"],),
        ).fetchone()
        assert row is not None
        dataset = MarketDataset.model_validate_json(json.dumps(row[0]))
        assert dataset.dataset_id == __import__("uuid").UUID(result["datasetId"])
        assert dataset.start.isoformat() == "2025-08-31T16:00:00+00:00"
        assert dataset.end.isoformat() == "2025-09-05T16:00:00+00:00"
        batches = conn.execute(
            "SELECT payload FROM raw_data_batch WHERE ingestion_task_id=%s", (result["taskId"],)
        ).fetchall()
        assert batches and set(dataset.batch_ids)
        bar_count = 0
        calendars = 0
        for (payload,) in batches:
            batch = DataBatch.model_validate_json(json.dumps(payload))
            assert batch.batch_id in dataset.batch_ids
            data = objects.get(
                batch.workspace_id, batch.object_key, expected_sha256=batch.content_sha256
            )
            records = ParquetRawRecordCodec().decode(data, expected_count=batch.record_count)
            for record in records:
                envelope = json.loads(record.payload)
                calendars += envelope["kind"] == "calendar"
                bar_count += envelope["kind"] == "bar"
        assert calendars > 0 and bar_count == result["expectedCount"]
        report = json.loads(
            objects.get(dataset.workspace_id, dataset.quality_report_key, expected_sha256=row[1])
        )
        assert report["bar_count"] == bar_count and report["coverage"]["missing_trading_days"] == []
        assert (
            report["coverage"]["expected_trading_days"]
            == report["coverage"]["observed_trading_days"]
            == 5
        )
        count = market_conn.execute(
            "SELECT count(*) FROM qtrade_market.market_bar WHERE workspace_id=%s AND dataset_id=%s",
            (dataset.workspace_id, dataset.dataset_id),
        ).fetchone()
        assert count == (bar_count,)
        print(
            f"PASS: {period}, {bar_count} actual archived source Bars, retained calendar, "
            "SHA-256 report, matching Timescale records"
        )
