"""Authorized bar-to-source lineage lookup across existing bounded domain services."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from qtrade_application.batches import RawBatchService
from qtrade_application.datasets import DatasetReplayService
from qtrade_application.market_data import MarketDataService
from qtrade_contracts import DataBatch
from qtrade_contracts.models import BarGranularity
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.ingestion import IngestionRepositoryPort, IngestionTask
from qtrade_domain.lineage import BarLineage
from qtrade_domain.ports import BarQuery, ErrorCode, PortError
from qtrade_domain.quality import QualityIssueStorePort

_INTERVALS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}


class DataLineageService:
    def __init__(
        self,
        datasets: DatasetReplayService,
        batches: RawBatchService,
        market: MarketDataService,
        collections: IngestionRepositoryPort,
        issues: QualityIssueStorePort,
    ):
        self._datasets = datasets
        self._batches = batches
        self._market = market
        self._collections = collections
        self._issues = issues

    def for_bar(
        self,
        access: WorkspaceAccess,
        dataset_id: UUID,
        instrument_id: str,
        granularity: BarGranularity,
        timestamp: datetime,
    ) -> BarLineage:
        access.require(Permission.READ)
        if (
            not isinstance(dataset_id, UUID)
            or not isinstance(instrument_id, str)
            or not 1 <= len(instrument_id) <= 256
            or granularity not in _INTERVALS
            or not isinstance(timestamp, datetime)
            or timestamp.utcoffset() is None
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid Bar lineage request")
        timestamp = timestamp.astimezone(UTC)
        workspace_id = access.workspace.workspace_id
        dataset = self._datasets.read(access, dataset_id)
        if (
            dataset.metadata.workspace_id != workspace_id
            or instrument_id not in dataset.metadata.instrument_ids
        ):
            raise PortError(ErrorCode.NOT_FOUND, "Bar lineage unavailable")
        page = self._market.query_bars(
            access,
            BarQuery(
                workspace_id,
                dataset_id,
                instrument_id,
                granularity,
                timestamp,
                timestamp + _INTERVALS[granularity],
                limit=10000,
            ),
        )
        bar = next(
            (item for item in page.bars if item.timestamp.astimezone(UTC) == timestamp), None
        )
        if bar is None or bar.batch_id not in dataset.metadata.batch_ids:
            raise PortError(ErrorCode.NOT_FOUND, "Bar lineage unavailable")
        archive = self._batches.read(access, bar.batch_id)
        batch = archive.metadata
        if (
            batch.workspace_id != workspace_id
            or batch.batch_id != bar.batch_id
            or batch.instrument_id != bar.instrument_id
            or batch.granularity != bar.granularity
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "Bar lineage source mismatch")
        collection = self._collections.get(workspace_id, batch.ingestion_task_id)
        if not self._task_matches(collection, batch):
            raise PortError(ErrorCode.UNAVAILABLE, "Bar lineage task mismatch")
        related_issues = tuple(
            issue
            for issue in self._issues.list_issues(workspace_id, dataset_id, 200)
            if issue.instrument_id == bar.instrument_id
            and issue.granularity == bar.granularity
            and issue.starts_at < timestamp + _INTERVALS[granularity]
            and timestamp < issue.ends_at
            and (not issue.batch_ids or batch.batch_id in issue.batch_ids)
        )
        return BarLineage(bar, dataset, batch, collection, related_issues)

    @staticmethod
    def _task_matches(task: IngestionTask, batch: DataBatch) -> bool:
        request = task.request
        return (
            request.workspace_id == batch.workspace_id
            and request.task_id == batch.ingestion_task_id
            and request.source == batch.source
            and request.authorization_id == batch.authorization_id
            and request.instrument_id == batch.instrument_id
            and request.granularity == batch.granularity
            and task.status == "completed"
            and batch.batch_id in task.batch_ids
        )
