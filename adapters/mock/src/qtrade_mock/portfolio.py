"""Deterministic portfolio repository for tests and local demonstrations."""

from uuid import UUID

from qtrade_domain.portfolio import PortfolioSnapshot


class MockPortfolioStore:
    def __init__(self, snapshots: tuple[PortfolioSnapshot, ...] = ()):
        self._snapshots = {item.workspace_id: item for item in snapshots}

    def get(self, workspace_id: UUID) -> PortfolioSnapshot | None:
        return self._snapshots.get(workspace_id)

    def put(self, snapshot: PortfolioSnapshot) -> None:
        self._snapshots[snapshot.workspace_id] = snapshot
