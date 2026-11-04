"""Compose a multi-instrument research dataset from published member datasets.

The composition is a selection step, not a new source: every output bar keeps the
provenance of a previously published immutable dataset.  Instrument ranking uses
observed traded value, so the selection can be reproduced from stored data.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from qtrade_application.datasets import DatasetReplayService
from qtrade_data_contracts import UniverseSnapshot
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.datasets import DatasetRepositoryPort, PublishedDataset, ReplayRequest
from qtrade_domain.ports import BarQuery, ErrorCode, MarketDataPort, PortError

MAX_SELECTION = 50
MAX_BARS = 5000


class SnapshotReader(Protocol):
    def snapshot(self, actor_id: UUID, scope_id: UUID, snapshot_id: UUID) -> UniverseSnapshot: ...


@dataclass(frozen=True)
class CompositionRequest:
    workspace_id: UUID
    snapshot_id: UUID
    start: datetime
    end: datetime
    selection_size: int = 20
    granularity: Literal["1m", "5m", "15m", "30m", "1h", "1d"] = "1d"
    transformation_version: str = "mairui.unadjusted-bars.v1"
    dataset_id: UUID | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.workspace_id, UUID)
            or not isinstance(self.snapshot_id, UUID)
            or any(
                not isinstance(item, datetime) or item.utcoffset() is None
                for item in (self.start, self.end)
            )
            or self.start >= self.end
            or type(self.selection_size) is not int
            or not 1 <= self.selection_size <= MAX_SELECTION
            or self.granularity not in ("1m", "5m", "15m", "30m", "1h", "1d")
            or not isinstance(self.transformation_version, str)
            or not self.transformation_version
            or (self.dataset_id is not None and not isinstance(self.dataset_id, UUID))
            or (
                self.created_at is not None
                and (
                    not isinstance(self.created_at, datetime) or self.created_at.utcoffset() is None
                )
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid dataset composition request")


@dataclass(frozen=True)
class CompositionMember:
    instrument_id: str
    average_value: str
    weeks: int


@dataclass(frozen=True)
class CompositionResult:
    dataset: PublishedDataset
    snapshot_id: UUID
    selected: tuple[CompositionMember, ...]
    excluded: tuple[str, ...] = field(default_factory=tuple)


class DatasetCompositionService:
    """Select liquid members of a frozen stock pool and replay them as one dataset."""

    def __init__(
        self,
        repository: DatasetRepositoryPort,
        replay: DatasetReplayService,
        market: MarketDataPort,
        snapshots: SnapshotReader | None,
    ):
        self._repository = repository
        self._replay = replay
        self._market = market
        self._snapshots = snapshots

    def compose(self, access: WorkspaceAccess, request: CompositionRequest) -> CompositionResult:
        access.require(Permission.DATA_MANAGE, object_workspace_id=request.workspace_id)
        if self._snapshots is None:
            raise PortError(ErrorCode.UNAVAILABLE, "stock pool catalog unavailable")
        snapshot = self._snapshots.snapshot(
            access.user_id, request.workspace_id, request.snapshot_id
        )
        members = tuple(
            member.instrument.instrument_id
            for member in snapshot.members
            if member.instrument.instrument_kind == "stock"
        )
        coverage: dict[str, tuple[PublishedDataset, ...]] = {}
        excluded: list[str] = []
        for instrument_id in members:
            datasets = self._coverage(access.workspace.workspace_id, instrument_id, request)
            if datasets is None:
                excluded.append(instrument_id)
                continue
            coverage[instrument_id] = datasets
        if len(coverage) < request.selection_size:
            raise PortError(
                ErrorCode.INVALID_INPUT,
                "stock pool does not contain enough instruments with complete coverage",
            )
        ranked: list[tuple[str, tuple[PublishedDataset, ...], str]] = []
        for instrument_id, datasets in coverage.items():
            average = self._average_value(access.workspace.workspace_id, instrument_id, datasets)
            ranked.append((instrument_id, datasets, average))
        ranked.sort(key=lambda item: (-Decimal(item[2]), item[0]))
        selected = ranked[: request.selection_size]
        budget = sum(
            self._bar_count(access.workspace.workspace_id, instrument_id, datasets)
            for instrument_id, datasets, _ in selected
        )
        if budget > MAX_BARS:
            raise PortError(
                ErrorCode.INVALID_INPUT,
                "composed dataset exceeds the runner bar budget",
            )
        batch_ids: list[UUID] = []
        for _, datasets, _ in selected:
            for dataset in datasets:
                for batch_id in dataset.metadata.batch_ids:
                    if batch_id not in batch_ids:
                        batch_ids.append(batch_id)
        first = min(dataset.metadata.start for _, datasets, _ in selected for dataset in datasets)
        last = max(dataset.metadata.end for _, datasets, _ in selected for dataset in datasets)
        # Deterministic identity keeps repeated selection of the same frozen
        # snapshot and range idempotent instead of publishing duplicate datasets.
        identity_parts = [
            "qtrade.compose.v1",
            str(request.workspace_id),
            str(snapshot.snapshot_id),
            request.start.isoformat(),
            request.end.isoformat(),
            request.transformation_version,
            str(request.selection_size),
            ",".join(instrument_id for instrument_id, _, _ in selected),
        ]
        if request.granularity != "1d":
            identity_parts.append(request.granularity)
        identity = ":".join(identity_parts)
        dataset_id = request.dataset_id or uuid5(NAMESPACE_URL, identity)
        chosen = tuple(
            CompositionMember(instrument_id, average, len(datasets))
            for instrument_id, datasets, average in selected
        )
        try:
            existing = self._repository.get(request.workspace_id, dataset_id)
        except PortError as error:
            if error.code != ErrorCode.NOT_FOUND:
                raise
        else:
            if (
                existing.metadata.batch_ids == tuple(batch_ids)
                and existing.metadata.transformation_version == request.transformation_version
                and existing.metadata.start.astimezone(UTC) == first
                and existing.metadata.end.astimezone(UTC) == last
            ):
                return CompositionResult(existing, snapshot.snapshot_id, chosen, tuple(excluded))
            raise PortError(
                ErrorCode.CONFLICT, "composed dataset identity reused by another selection"
            )
        replay_request = ReplayRequest(
            request.workspace_id,
            dataset_id,
            tuple(batch_ids),
            request.transformation_version,
            first,
            last,
            request.created_at or datetime.now(UTC),
        )
        published = self._replay.replay(access, replay_request)
        return CompositionResult(published, snapshot.snapshot_id, chosen, tuple(excluded))

    def _coverage(
        self, workspace_id: UUID, instrument_id: str, request: CompositionRequest
    ) -> tuple[PublishedDataset, ...] | None:
        items: list[PublishedDataset] = []
        before: UUID | None = None
        while True:
            page = self._repository.for_instrument(
                workspace_id,
                instrument_id,
                granularity=request.granularity,
                limit=200,
                before=before,
            )
            if not page:
                break
            items.extend(page)
            before = page[-1].metadata.dataset_id
            if len(page) < 200:
                break
        matching = sorted(
            (
                item
                for item in items
                # Composed datasets also appear under each member instrument.
                # Only single-instrument publications are valid sources, or a
                # second composition would overlap its own previous output.
                if item.metadata.instrument_ids == (instrument_id,)
                and item.metadata.transformation_version == request.transformation_version
                and item.metadata.granularity == request.granularity
                and item.metadata.start < request.end
                and item.metadata.end > request.start
            ),
            key=lambda item: (item.metadata.start, item.metadata.dataset_id),
        )
        if not matching:
            return None
        if matching[0].metadata.start > request.start or matching[-1].metadata.end < request.end:
            return None
        for previous, current in zip(matching, matching[1:], strict=False):
            if previous.metadata.end != current.metadata.start:
                return None
        return tuple(matching)

    def _bar_count(
        self,
        workspace_id: UUID,
        instrument_id: str,
        datasets: tuple[PublishedDataset, ...],
    ) -> int:
        total = 0
        for dataset in datasets:
            metadata = dataset.metadata
            page = self._market.query_bars(
                BarQuery(
                    workspace_id,
                    metadata.dataset_id,
                    instrument_id,
                    metadata.granularity,
                    metadata.start,
                    metadata.end,
                    limit=100,
                )
            )
            total += len(page.bars)
        return total

    def _average_value(
        self,
        workspace_id: UUID,
        instrument_id: str,
        datasets: tuple[PublishedDataset, ...],
    ) -> str:
        totals = []
        for dataset in datasets[-4:]:
            metadata = dataset.metadata
            page = self._market.query_bars(
                BarQuery(
                    workspace_id,
                    metadata.dataset_id,
                    instrument_id,
                    metadata.granularity,
                    metadata.start,
                    metadata.end,
                    limit=100,
                )
            )
            for bar in page.bars:
                totals.append(
                    bar.value if bar.value is not None and bar.value > 0 else bar.close * bar.volume
                )
        if not totals:
            return "0"
        return format(sum(totals) / len(totals), "f")
