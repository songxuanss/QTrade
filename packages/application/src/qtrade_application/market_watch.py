"""Workspace-scoped latest stored Bar snapshots for a user-selected watchlist."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from qtrade_application.indicators import IndicatorSeries, calculate_indicators
from qtrade_application.market_data import MarketDataService
from qtrade_contracts import Bar
from qtrade_contracts.models import BarGranularity, Granularity
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.datasets import DatasetRepositoryPort, PublishedDataset
from qtrade_domain.ports import BarQuery, ErrorCode, PortError


@dataclass(frozen=True)
class WatchSnapshot:
    instrument_id: str
    dataset_id: str | None
    granularity: str | None
    latest: Bar | None
    previous: Bar | None
    available_granularities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketChartData:
    bars: tuple[Bar, ...]
    indicators: IndicatorSeries


class MarketWatchService:
    def __init__(self, datasets: DatasetRepositoryPort, market: MarketDataService | None):
        self._datasets, self._market = datasets, market

    def _instrument_datasets(
        self,
        workspace_id: UUID,
        instrument_id: str,
        granularity: Granularity | None = None,
    ) -> tuple[PublishedDataset, ...]:
        # Scope/instrument filters precede the page limit. The cursor is an internal
        # stable identity; no global recent-dataset scan can hide an older stock.
        result: list[PublishedDataset] = []
        before: UUID | None = None
        for _ in range(100):
            page = self._datasets.for_instrument(
                workspace_id, instrument_id, granularity=granularity, limit=200, before=before
            )
            if (
                not isinstance(page, tuple)
                or len(page) > 200
                or any(
                    not isinstance(item, PublishedDataset)
                    or item.metadata.workspace_id != workspace_id
                    or instrument_id not in item.metadata.instrument_ids
                    or (granularity is not None and item.metadata.granularity != granularity)
                    for item in page
                )
            ):
                raise PortError(ErrorCode.UNAVAILABLE, "invalid instrument dataset scope")
            identities = [item.metadata.dataset_id for item in page]
            if (
                len(set(identities)) != len(identities)
                or identities != sorted(identities, key=lambda value: value.int, reverse=True)
                or (
                    before is not None
                    and any(identity.int >= before.int for identity in identities)
                )
            ):
                raise PortError(ErrorCode.UNAVAILABLE, "invalid instrument dataset page")
            result.extend(page)
            if len(page) < 200:
                return tuple(result)
            before = identities[-1]
        raise PortError(ErrorCode.UNAVAILABLE, "instrument dataset page limit exceeded")

    def snapshots(
        self, access: WorkspaceAccess, instrument_ids: tuple[str, ...]
    ) -> tuple[WatchSnapshot, ...]:
        access.require(Permission.READ)
        if (
            not isinstance(instrument_ids, tuple)
            or len(instrument_ids) > 50
            or any(
                not isinstance(item, str) or not 1 <= len(item) <= 256 for item in instrument_ids
            )
            or len(set(instrument_ids)) != len(instrument_ids)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid watchlist")
        workspace_id = access.workspace.workspace_id
        results: list[WatchSnapshot] = []
        for instrument_id in instrument_ids:
            candidates = self._instrument_datasets(workspace_id, instrument_id)
            if not candidates or self._market is None:
                results.append(WatchSnapshot(instrument_id, None, None, None, None))
                continue
            selected = max(
                candidates,
                key=lambda item: (item.metadata.created_at, item.metadata.version),
            )
            metadata = selected.metadata
            if metadata.granularity == "tick":
                results.append(
                    WatchSnapshot(
                        instrument_id,
                        str(metadata.dataset_id),
                        metadata.granularity,
                        None,
                        None,
                        tuple(
                            sorted(
                                {
                                    item.metadata.granularity
                                    for item in candidates
                                    if item.metadata.granularity != "tick"
                                }
                            )
                        ),
                    )
                )
                continue
            try:
                bars = self.chart(access, instrument_id, metadata.granularity, 7).bars
            except PortError as error:
                if error.code != ErrorCode.NOT_FOUND:
                    raise
                bars = ()
            results.append(
                WatchSnapshot(
                    instrument_id,
                    str(bars[-1].dataset_id) if bars else str(metadata.dataset_id),
                    metadata.granularity,
                    bars[-1] if bars else None,
                    bars[-2] if len(bars) > 1 else None,
                    tuple(
                        sorted(
                            {
                                item.metadata.granularity
                                for item in candidates
                                if item.metadata.granularity != "tick"
                            }
                        )
                    ),
                )
            )
        return tuple(results)

    def chart(
        self,
        access: WorkspaceAccess,
        instrument_id: str,
        granularity: BarGranularity,
        days: int,
        *,
        dataset_id: UUID | None = None,
    ) -> MarketChartData:
        access.require(Permission.READ)
        if (
            not isinstance(instrument_id, str)
            or not 1 <= len(instrument_id) <= 256
            or granularity not in ("1m", "5m", "15m", "30m", "1h", "1d")
            or days not in (1, 7, 30)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid chart query")
        if self._market is None:
            raise PortError(ErrorCode.UNAVAILABLE, "market data is unavailable")
        workspace_id = access.workspace.workspace_id
        if dataset_id is not None:
            if not isinstance(dataset_id, UUID):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid chart dataset")
            # A result link names a frozen consumer dataset. Never fall back to
            # newer observations from the same instrument after this lookup.
            selected = self._datasets.get(workspace_id, dataset_id)
            metadata = selected.metadata
            if metadata.workspace_id != workspace_id or metadata.dataset_id != dataset_id:
                raise PortError(ErrorCode.UNAVAILABLE, "invalid chart dataset scope")
            if instrument_id not in metadata.instrument_ids or metadata.granularity != granularity:
                raise PortError(ErrorCode.NOT_FOUND, "chart dataset does not match")
            candidates = [selected]
        else:
            candidates = list(self._instrument_datasets(workspace_id, instrument_id, granularity))
        if not candidates:
            raise PortError(ErrorCode.NOT_FOUND, "chart dataset unavailable")
        end = max(item.metadata.end for item in candidates)
        earliest = min(item.metadata.start for item in candidates)
        # Request ends may include holidays or future dates. Search back to the
        # latest available observations instead of presenting an empty terminal.
        windows = sorted({days, max(days, 7), max(days, 30)})
        starts = [max(earliest, end - timedelta(days=window)) for window in windows]
        if earliest not in starts:
            starts.append(earliest)
        candidates.sort(
            key=lambda item: (
                item.metadata.created_at,
                item.metadata.version,
                str(item.metadata.dataset_id),
            )
        )
        for start in dict.fromkeys(starts):
            collected: dict[object, Bar] = {}
            for item in candidates:
                metadata = item.metadata
                left, right = max(start, metadata.start), min(end, metadata.end)
                if left >= right:
                    continue
                cursor: str | None = None
                seen_cursors: set[str] = set()
                for _ in range(6):
                    page = self._market.query_bars(
                        access,
                        BarQuery(
                            workspace_id,
                            metadata.dataset_id,
                            instrument_id,
                            granularity,
                            left,
                            right,
                            10_000,
                            cursor,
                        ),
                    )
                    for bar in page.bars:
                        collected[bar.timestamp] = bar
                    if len(collected) > 50_000:
                        raise PortError(ErrorCode.UNAVAILABLE, "chart page limit exceeded")
                    if page.next_cursor is None:
                        break
                    if page.next_cursor in seen_cursors:
                        raise PortError(ErrorCode.UNAVAILABLE, "chart page limit exceeded")
                    seen_cursors.add(page.next_cursor)
                    cursor = page.next_cursor
                else:
                    raise PortError(ErrorCode.UNAVAILABLE, "chart page limit exceeded")
            if collected:
                ordered = sorted(collected.values(), key=lambda bar: bar.timestamp)
                interval = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400}
                observed_end = ordered[-1].timestamp + timedelta(seconds=interval[granularity])
                observed_start = observed_end - timedelta(days=days)
                result = tuple(bar for bar in ordered if bar.timestamp >= observed_start)
                return MarketChartData(result, calculate_indicators(result))
        return MarketChartData((), calculate_indicators(()))
