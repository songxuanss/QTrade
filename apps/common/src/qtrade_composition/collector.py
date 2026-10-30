"""QTrade consumes remote immutable publications; no provider SDK or credentials here."""

import json
import logging
import os
from collections.abc import Mapping
from hashlib import sha256
from uuid import UUID, uuid5

import psycopg

from qtrade_application.batches import RawBatchService
from qtrade_contracts import Bar, MarketDataset
from qtrade_data_contracts.collection import (
    CollectedBar,
    CollectionPublication,
    CollectionRecordPage,
    digest,
)
from qtrade_data_core.ports import DataFailure
from qtrade_data_http.client import CollectorClient
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.batches import BatchInput
from qtrade_domain.datasets import PublishedDataset
from qtrade_domain.market_data import dataset_digest
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.raw_records import RawRecord
from qtrade_object_store.s3 import S3ObjectStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec
from qtrade_postgres.batches import PostgresBatchRepository
from qtrade_postgres.datasets import PostgresDatasetRepository
from qtrade_postgres.market_data import TimescaleMarketData
from qtrade_postgres.workspaces import PostgresWorkspaceStore

logger = logging.getLogger(__name__)


def collector_client_from_env(env: Mapping[str, str] = os.environ) -> CollectorClient | None:
    if not env.get("QTRADE_COLLECTOR_URL") or not env.get("COLLECTOR_SERVICE_TOKEN"):
        return None
    return CollectorClient(
        env["QTRADE_COLLECTOR_URL"],
        env["COLLECTOR_SERVICE_TOKEN"],
        allow_http=env.get("QTRADE_COLLECTOR_ALLOW_HTTP") == "true",
    )


class CollectorImportStatus:
    def __init__(self, url: str):
        self.url = url

    def record(
        self, publication: CollectionPublication, state: str, error: str | None = None
    ) -> None:
        with psycopg.connect(self.url) as conn:
            conn.execute(
                """INSERT INTO
                collector_import(workspace_id,publication_id,state,error_code,attempts,manifest)
                VALUES(%s,%s,%s,%s,1,%s::jsonb) ON CONFLICT(workspace_id,publication_id)
                DO UPDATE SET state=excluded.state,error_code=excluded.error_code,
                attempts=collector_import.attempts+1,updated_at=now()""",
                (
                    publication.scope_id,
                    publication.publication_id,
                    state,
                    error,
                    publication.model_dump_json(),
                ),
            )

    def state(self, scope: UUID, publication: UUID) -> dict[str, object]:
        with psycopg.connect(self.url) as conn:
            row = conn.execute(
                """SELECT state,error_code FROM collector_import WHERE workspace_id=%s AND
                publication_id=%s""",
                (scope, publication),
            ).fetchone()
            return (
                {
                    "state": row[0],
                    "error_code": row[1],
                    # This consumer publishes MarketDataset using the public
                    # publication identity; other consumers may choose another.
                    "dataset_id": str(publication) if row[0] == "visible" else None,
                }
                if row
                else {"state": "waiting", "error_code": None}
            )

    def names(self, scope: UUID) -> dict[str, str]:
        with psycopg.connect(self.url) as conn:
            rows = conn.execute(
                """SELECT DISTINCT ON (manifest->>'instrument_id')
                manifest->>'instrument_id',manifest->>'instrument_name' FROM collector_import
                WHERE workspace_id=%s ORDER BY manifest->>'instrument_id',updated_at DESC""",
                (scope,),
            ).fetchall()
            return {str(row[0]): str(row[1]) for row in rows if row[1]}


class CollectorImporter:
    def __init__(self, client: CollectorClient, env: Mapping[str, str]):
        self.client, self.env = client, env
        self.status = CollectorImportStatus(env["DATABASE_URL"])
        self.workspaces = PostgresWorkspaceStore(env["DATABASE_URL"])
        self.datasets = PostgresDatasetRepository(env["DATABASE_URL"])
        self.market = TimescaleMarketData(env["MARKET_DATABASE_URL"])
        self.objects = S3ObjectStore.connect(
            bucket=env.get("OBJECT_STORE_BUCKET", "qtrade"),
            endpoint_url=env.get("OBJECT_STORE_URL"),
            allow_http=env.get("QTRADE_ENV") == "local",
            access_key=env.get("OBJECT_STORE_ACCESS_KEY"),
            secret_key=env.get("OBJECT_STORE_SECRET_KEY"),
        )
        self.batches = RawBatchService(
            PostgresBatchRepository(env["DATABASE_URL"]), self.objects, ParquetRawRecordCodec()
        )

    def consume(self) -> int:
        deliveries = self.client.request(
            "GET", "/v1/deliveries", params={"consumer_id": "qtrade-market"}
        )
        count = 0
        for value in deliveries:
            publication = CollectionPublication.model_validate_json(json.dumps(value))
            access = self.workspaces.access(publication.actor_id, publication.scope_id)
            if access is None:
                continue
            try:
                access.require(Permission.DATA_MANAGE)
                self.import_publication(access, publication)
                self.status.record(publication, "visible")
                self.client.request(
                    "POST",
                    f"/v1/deliveries/{publication.publication_id}/ack",
                    actor_id=access.user_id,
                    scope_id=publication.scope_id,
                    params={"consumer_id": "qtrade-market"},
                )
                count += 1
            except (PortError, DataFailure, ValueError) as error:
                code = (
                    error.code.value
                    if isinstance(error, PortError)
                    else error.code
                    if isinstance(error, DataFailure)
                    else "unverified"
                )
                self.status.record(publication, "failed", code)
                logger.warning(
                    "collector import deferred: publication=%s correlation=%s code=%s",
                    publication.publication_id,
                    publication.correlation_id,
                    code,
                )
        return count

    def import_publication(
        self, access: WorkspaceAccess, publication: CollectionPublication
    ) -> None:
        access.require(Permission.DATA_MANAGE, object_workspace_id=publication.scope_id)
        if publication.dataset_kind != "bars":
            raise DataFailure("unsupported")
        fingerprint = digest(publication.model_dump(mode="json"))
        try:
            existing = self.datasets.get(publication.scope_id, publication.publication_id)
        except PortError as error:
            if error.code != ErrorCode.NOT_FOUND:
                raise
        else:
            if existing.request_sha256 != fingerprint:
                raise DataFailure("conflict")
            return
        records: list[CollectedBar] = []
        offset = 0
        while True:
            result = self.client.request(
                "GET",
                f"/v1/publications/{publication.publication_id}/records",
                actor_id=access.user_id,
                scope_id=publication.scope_id,
                params={"offset": offset, "limit": 1000},
            )
            page = CollectionRecordPage.model_validate_json(json.dumps(result))
            if (
                page.scope_id != publication.scope_id
                or page.publication_id != publication.publication_id
                or page.offset != offset
                or not page.records
            ):
                raise DataFailure("unverified")
            for record in page.records:
                if (
                    not isinstance(record, CollectedBar)
                    or record.instrument_id != publication.instrument_id
                    or record.granularity != publication.granularity
                    or not publication.start <= record.timestamp < publication.end
                    or records
                    and record.timestamp <= records[-1].timestamp
                ):
                    raise DataFailure("unverified")
                records.append(record)
            if len(records) > publication.record_count:
                raise DataFailure("unverified")
            if page.next_offset is None:
                break
            if page.next_offset != offset + len(page.records):
                raise DataFailure("unverified")
            offset = page.next_offset
        if (
            len(records) != publication.record_count
            or digest([item.model_dump(mode="json") for item in records])
            != publication.records_sha256
        ):
            raise DataFailure("unverified")
        batch_id = uuid5(publication.publication_id, "qtrade-import.v1")
        bars = tuple(
            Bar(
                schema_version=1,
                workspace_id=publication.scope_id,
                instrument_id=item.instrument_id,
                specification_version=1,
                timestamp=item.timestamp,
                granularity=item.granularity,
                open=item.open,
                high=item.high,
                low=item.low,
                close=item.close,
                volume=item.volume,
                value=item.value,
                quality=item.quality,
                batch_id=batch_id,
                dataset_id=publication.publication_id,
            )
            for item in records
        )
        self.batches.publish(
            access,
            BatchInput(
                publication.scope_id,
                batch_id,
                "collector.standard-bars",
                "collector-service",
                publication.part_id,
                publication.instrument_id,
                publication.granularity,
                publication.published_at,
                tuple(
                    RawRecord(index, item.model_dump_json().encode())
                    for index, item in enumerate(bars)
                ),
            ),
        )
        content_hash = dataset_digest(bars)
        expected_dates = publication.evidence.get("expected_dates")
        observed_dates = publication.evidence.get("observed_dates")
        report = json.dumps(
            {
                "schema_version": 1,
                "dataset_id": str(publication.publication_id),
                "collector_publication": publication.model_dump(mode="json"),
                "checks": ["page_sha256", "records_sha256", "range", "identity", "monotonic_time"],
                "coverage": {
                    **publication.evidence,
                    "research_readiness": "unverified",
                    "coverage_status": "unverified",
                    "expected_trading_days": len(expected_dates)
                    if isinstance(expected_dates, list)
                    else None,
                    "observed_trading_days": len(observed_dates)
                    if isinstance(observed_dates, list)
                    else None,
                    "missing_trading_days": publication.evidence.get("missing_dates", []),
                    "observed_start": records[0].timestamp.isoformat(),
                    "observed_end": records[-1].timestamp.isoformat(),
                    "limitations": list(publication.limitations),
                },
                "bar_count": len(bars),
                "content_sha256": content_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        report_hash = sha256(report).hexdigest()
        report_key = f"datasets/{publication.publication_id}/quality/{report_hash}.json"
        self.objects.put(
            publication.scope_id,
            report_key,
            report,
            content_type="application/json",
            expected_sha256=report_hash,
        )
        self.objects.get(publication.scope_id, report_key, expected_sha256=report_hash)
        metadata = MarketDataset(
            schema_version=1,
            workspace_id=publication.scope_id,
            dataset_id=publication.publication_id,
            version=1,
            created_at=publication.published_at,
            batch_ids=(batch_id,),
            instrument_ids=(publication.instrument_id,),
            granularity=publication.granularity,
            start=publication.start,
            end=publication.end,
            transformation_version=publication.mapping_version,
            quality_report_key=report_key,
            content_sha256=content_hash,
        )
        self.market.write_bars(publication.scope_id, publication.publication_id, bars)
        self.datasets.put(
            access.user_id, PublishedDataset(metadata, fingerprint, None, report_hash)
        )
