"""Authorized dataset operations and defensive validation of market-data adapters."""

from uuid import UUID

from qtrade_contracts import Bar
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.market_data import dataset_bars
from qtrade_domain.ports import BarPage, BarQuery, ErrorCode, MarketDataPort, PortError


class MarketDataService:
    def __init__(self, store: MarketDataPort):
        self._store = store

    def write_bars(self, access: WorkspaceAccess, dataset_id: UUID, bars: tuple[Bar, ...]) -> None:
        access.require(Permission.DATA_MANAGE)
        workspace_id = access.workspace.workspace_id
        ordered = dataset_bars(workspace_id, dataset_id, bars)
        self._store.write_bars(workspace_id, dataset_id, ordered)

    def query_bars(self, access: WorkspaceAccess, query: BarQuery) -> BarPage:
        access.require(Permission.READ, object_workspace_id=query.workspace_id)
        page = self._store.query_bars(query)
        if not isinstance(page, BarPage):
            raise PortError(ErrorCode.UNAVAILABLE, "market data adapter returned invalid page")
        bars = page.bars
        if (
            not isinstance(bars, tuple)
            or len(bars) > query.limit
            or any(
                not isinstance(bar, Bar)
                or bar.workspace_id != query.workspace_id
                or bar.dataset_id != query.dataset_id
                or bar.instrument_id != query.instrument_id
                or bar.granularity != query.granularity
                or not query.start <= bar.timestamp < query.end
                for bar in bars
            )
            or any(a.timestamp >= b.timestamp for a, b in zip(bars, bars[1:], strict=False))
            or (
                page.next_cursor is not None
                and (
                    not isinstance(page.next_cursor, str)
                    or not 1 <= len(page.next_cursor) <= 512
                    or not bars
                )
            )
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "market data adapter returned invalid page")
        return page
