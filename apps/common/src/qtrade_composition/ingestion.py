"""Production composition for the historical collection application service."""

import os
from collections.abc import Mapping
from datetime import UTC, datetime

from qtrade_application.batches import RawBatchService
from qtrade_application.datasets import DatasetReplayService
from qtrade_application.ingestion import HistoricalIngestionWorker
from qtrade_domain.bar_json import StandardBarJsonTransformation
from qtrade_domain.history_sources import HistoricalSource
from qtrade_domain.ingestion import CollectionPolicy
from qtrade_mairui import MaiRuiAShareTickProvider
from qtrade_mairui.bar_quality import MaiRuiBarQuality
from qtrade_mairui.history_bars import MaiRuiBarTransformation, MaiRuiHistoricalBars
from qtrade_mairui.minute_bars import MaiRuiMinuteBars
from qtrade_mairui.trade_ticks import MaiRuiTradeTicks
from qtrade_object_store.s3 import S3ObjectStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec
from qtrade_postgres.batches import PostgresBatchRepository
from qtrade_postgres.datasets import PostgresDatasetRepository
from qtrade_postgres.ingestion import PostgresIngestionRepository
from qtrade_postgres.market_data import TimescaleMarketData


def historical_ingestion_from_env(
    env: Mapping[str, str] = os.environ,
) -> HistoricalIngestionWorker | None:
    licence = env.get("MAIRUI_LICENCE")
    if not licence:
        return None
    database_url = env["DATABASE_URL"]
    market_url = env["MARKET_DATABASE_URL"]
    bucket = env.get("OBJECT_STORE_BUCKET", "qtrade")
    endpoint = env.get("OBJECT_STORE_URL")
    objects = S3ObjectStore.connect(
        bucket=bucket,
        endpoint_url=endpoint,
        allow_http=bool(endpoint and endpoint.startswith("http://")),
        access_key=env.get("OBJECT_STORE_ACCESS_KEY"),
        secret_key=env.get("OBJECT_STORE_SECRET_KEY"),
    )
    if env.get("QTRADE_ENV") == "local":
        objects.ensure_bucket()
    ingestion_repo = PostgresIngestionRepository(database_url)
    batch_repo = PostgresBatchRepository(database_url)
    raw_batches = RawBatchService(batch_repo, objects, ParquetRawRecordCodec())
    replay = DatasetReplayService(
        PostgresDatasetRepository(database_url),
        raw_batches,
        TimescaleMarketData(market_url),
        objects,
        (StandardBarJsonTransformation(), MaiRuiBarTransformation()),
        (MaiRuiBarQuality(),),
    )
    return HistoricalIngestionWorker(
        ingestion_repo,
        raw_batches,
        replay,
        (
            MaiRuiAShareTickProvider(licence),
            MaiRuiHistoricalBars(licence, pro=env.get("QTRADE_MAIRUI_PRO_ENABLED") == "true"),
        ),
        policy=CollectionPolicy(
            min_interval_seconds=float(env.get("QTRADE_COLLECTION_MIN_INTERVAL_SECONDS", "3")),
            retry_base_seconds=30,
        ),
        specifications=(
            HistoricalSource(
                "mairui.a-share.tick",
                "mairui-default",
                ("tick",),
                datetime(2026, 8, 12, 16, tzinfo=UTC),
            ),
            HistoricalSource(
                "mairui.a-share.bar",
                "mairui-default",
                ("1m", "5m", "15m", "30m", "1h", "1d")
                if env.get("QTRADE_MAIRUI_PRO_ENABLED") == "true"
                else ("5m", "15m", "30m", "1h", "1d"),
            ),
        ),
    )


def research_replay_from_env(env: Mapping[str, str] = os.environ) -> DatasetReplayService:
    objects = S3ObjectStore.connect(
        bucket=env.get("OBJECT_STORE_BUCKET", "qtrade"),
        endpoint_url=env.get("OBJECT_STORE_URL"),
        allow_http=env.get("QTRADE_ENV") == "local",
        access_key=env.get("OBJECT_STORE_ACCESS_KEY"),
        secret_key=env.get("OBJECT_STORE_SECRET_KEY"),
    )
    return DatasetReplayService(
        PostgresDatasetRepository(env["DATABASE_URL"]),
        RawBatchService(
            PostgresBatchRepository(env["DATABASE_URL"]), objects, ParquetRawRecordCodec()
        ),
        TimescaleMarketData(env["MARKET_DATABASE_URL"]),
        objects,
        (MaiRuiMinuteBars(), MaiRuiTradeTicks(), MaiRuiBarTransformation()),
        (MaiRuiBarQuality(),),
    )
