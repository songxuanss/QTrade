"""Compatibility adapter: portable campaign commands into existing single-stock ingestion."""

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid5

from qtrade_application.batches import RawBatchService
from qtrade_application.data_management import DataManagementService
from qtrade_application.workspaces import WorkspaceService
from qtrade_composition.data_catalog import QTradeDataAuthorizer
from qtrade_composition.downloads import tick_download_policy_from_env
from qtrade_composition.ingestion import historical_ingestion_from_env
from qtrade_data_contracts import CollectionRange, CollectionSelection, ReferenceIdentity
from qtrade_data_contracts.models import ErrorCode as DataErrorCode
from qtrade_data_core.campaigns import CollectionGateway, Coverage, Work
from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.campaigns import PostgresCampaigns
from qtrade_data_postgres.universes import PostgresUniverses
from qtrade_domain.authorization import Permission
from qtrade_domain.ingestion import CollectionRequest
from qtrade_domain.ports import BarQuery, ErrorCode, PortError
from qtrade_object_store.s3 import S3ObjectStore
from qtrade_parquet.raw_records import ParquetRawRecordCodec
from qtrade_postgres.batches import PostgresBatchRepository
from qtrade_postgres.datasets import PostgresDatasetRepository
from qtrade_postgres.ingestion import PostgresIngestionRepository
from qtrade_postgres.market_data import TimescaleMarketData
from qtrade_postgres.quality import PostgresQualityIssueStore
from qtrade_postgres.workspaces import PostgresWorkspaceStore


def failure(error: PortError) -> DataFailure:
    codes: dict[ErrorCode, DataErrorCode] = {
        ErrorCode.NOT_FOUND: "not_found",
        ErrorCode.CONFLICT: "conflict",
        ErrorCode.REJECTED: "forbidden",
        ErrorCode.INVALID_INPUT: "invalid_input",
        ErrorCode.UNSUPPORTED: "unsupported",
    }
    return DataFailure(codes.get(error.code, "unavailable"))


class ExistingCollectionExecutor:
    def __init__(
        self,
        workspaces: WorkspaceService,
        service: DataManagementService,
        tasks: PostgresIngestionRepository,
        batches: RawBatchService,
        datasets: PostgresDatasetRepository,
        market: TimescaleMarketData,
    ):
        (
            self._workspaces,
            self._service,
            self._tasks,
            self._batches,
            self._datasets,
            self._market,
        ) = workspaces, service, tasks, batches, datasets, market

    def _request(
        self,
        selection: CollectionSelection,
        instrument_id: str,
        task_id: UUID,
        start: datetime,
        end: datetime,
        created_at: datetime,
    ) -> CollectionRequest:
        return CollectionRequest(
            selection.scope_id,
            task_id,
            selection.source,
            selection.authorization_id,
            instrument_id,
            selection.granularity,
            start,
            end,
            created_at,
            selection.transformation_version,
        )

    def coverage(self, selection: CollectionSelection, instrument: ReferenceIdentity) -> Coverage:
        if instrument.instrument_kind != "stock":
            raise DataFailure("unsupported")
        try:
            access = self._workspaces.authorize(
                selection.actor_id, selection.scope_id, Permission.DATA_MANAGE
            )
            request = self._request(
                selection,
                instrument.instrument_id,
                uuid5(selection.request_id, instrument.instrument_id),
                selection.start,
                selection.end,
                datetime.now(UTC),
            )
            plan = self._service.collection_plan(access, request)
            completed = tuple(
                sorted(
                    (
                        task.request.task_id
                        for task in plan.overlapping
                        if task.status == "completed"
                    ),
                    key=str,
                )
            )
            payload = sorted(
                (
                    str(task.request.task_id),
                    "completed" if task.status == "completed" else "reserved",
                    task.request.start.isoformat(),
                    task.request.end.isoformat(),
                )
                for task in plan.overlapping
            )
            return Coverage(
                tuple(
                    CollectionRange(start=task.request.start, end=task.request.end)
                    for task in plan.overlapping
                    if task.status == "completed"
                ),
                tuple(CollectionRange(start=start, end=end) for start, end in plan.submit_ranges),
                tuple(sorted((task.request.task_id for task in plan.reuse), key=str)),
                completed,
                sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest(),
            )
        except PortError as error:
            raise failure(error) from None

    def submit(self, selection: CollectionSelection, work: Work, created_at: datetime) -> None:
        try:
            access = self._workspaces.authorize(
                selection.actor_id, selection.scope_id, Permission.DATA_MANAGE
            )
            if work.reuse:
                task = self._tasks.get(selection.scope_id, work.task_id)
                if task.status == "failed":
                    self._service.rerun_task(access, work.task_id, datetime.now(UTC))
                return
            request = self._request(
                selection,
                work.instrument.instrument_id,
                work.task_id,
                work.start,
                work.end,
                created_at,
            )
            self._service.submit_task(access, request, avoid_overlap=selection.mode == "missing")
        except PortError as error:
            raise failure(error) from None

    def result(self, selection: CollectionSelection, task_id: UUID) -> dict[str, object]:
        try:
            access = self._workspaces.authorize(
                selection.actor_id, selection.scope_id, Permission.READ
            )
            task = self._tasks.get(selection.scope_id, task_id)
            bounds = {"start": task.request.start.isoformat(), "end": task.request.end.isoformat()}
            if task.status != "completed":
                return {
                    **bounds,
                    "state": task.status,
                    "error_code": task.last_error_code.value if task.last_error_code else None,
                    "next_attempt_at": task.next_attempt_at.isoformat(),
                }
            archives = tuple(self._batches.read(access, batch_id) for batch_id in task.batch_ids)
            raw_count = sum(batch.metadata.record_count for batch in archives)
            observed_count = raw_count
            if task.request.source == "mairui.a-share.bar":
                observed_count = sum(
                    json.loads(record.payload).get("kind") == "bar"
                    for batch in archives
                    for record in batch.records
                )
            if observed_count == 0:
                return {
                    **bounds,
                    "state": "no_observation",
                    "raw_records": raw_count,
                    "observed_records": 0,
                    "batch_ids": [str(i) for i in task.batch_ids],
                }
            result: dict[str, object] = {
                **bounds,
                "state": "collected",
                "raw_records": raw_count,
                "observed_records": observed_count,
                "batch_ids": [str(i) for i in task.batch_ids],
                "dataset_id": str(task.dataset_id) if task.dataset_id else None,
            }
            if task.dataset_id and task.request.granularity != "tick":
                metadata = self._datasets.get(selection.scope_id, task.dataset_id).metadata
                bars = self._market.query_bars(
                    BarQuery(
                        selection.scope_id,
                        task.dataset_id,
                        task.request.instrument_id,
                        task.request.granularity,
                        metadata.start,
                        metadata.end,
                        1,
                        None,
                    )
                ).bars
                if bars:
                    result["state"] = "viewable"
            return result
        except PortError as error:
            return {"state": "failed", "error_code": failure(error).code}

    def retry(self, selection: CollectionSelection, task_id: UUID) -> None:
        try:
            access = self._workspaces.authorize(
                selection.actor_id, selection.scope_id, Permission.DATA_MANAGE
            )
            self._service.rerun_task(access, task_id, datetime.now(UTC))
        except PortError as error:
            raise failure(error) from None


def collection_gateway_from_env(
    workspaces: WorkspaceService | None = None, env: Mapping[str, str] = os.environ
) -> CollectionGateway | None:
    ingestion = historical_ingestion_from_env(env)
    if ingestion is None:
        return None
    database = env["DATABASE_URL"]
    data_database = env.get("DATA_COLLECTION_DATABASE_URL", database)
    workspaces = workspaces or WorkspaceService(PostgresWorkspaceStore(database))
    tasks = PostgresIngestionRepository(database)
    objects = S3ObjectStore.connect(
        bucket=env.get("OBJECT_STORE_BUCKET", "qtrade"),
        endpoint_url=env.get("OBJECT_STORE_URL"),
        allow_http=env.get("QTRADE_ENV") == "local",
        access_key=env.get("OBJECT_STORE_ACCESS_KEY"),
        secret_key=env.get("OBJECT_STORE_SECRET_KEY"),
    )
    executor = ExistingCollectionExecutor(
        workspaces,
        DataManagementService(
            tasks,
            PostgresBatchRepository(database),
            PostgresQualityIssueStore(database),
            ingestion,
            download_policy=tick_download_policy_from_env(env),
        ),
        tasks,
        RawBatchService(PostgresBatchRepository(database), objects, ParquetRawRecordCodec()),
        PostgresDatasetRepository(database),
        TimescaleMarketData(env["MARKET_DATABASE_URL"]),
    )
    return CollectionGateway(
        QTradeDataAuthorizer(workspaces),
        PostgresUniverses(data_database),
        PostgresCampaigns(data_database),
        executor,
    )
