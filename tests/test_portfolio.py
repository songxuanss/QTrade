from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from qtrade_application.portfolio import PortfolioService
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.portfolio import PortfolioPosition, PortfolioSnapshot
from qtrade_mock.portfolio import MockPortfolioStore


def test_portfolio_summary_calculates_equity_exposure_concentration_and_drawdown():
    now = datetime.now(UTC)
    workspace = Workspace(uuid4(), "Personal", "personal", None, False, False, now)
    snapshot = PortfolioSnapshot(
        workspace.workspace_id,
        "DEMO-PAPER-01",
        "USD",
        Decimal("25000"),
        (
            PortfolioPosition("TEST:A", Decimal("10"), Decimal("99"), Decimal("101")),
            PortfolioPosition("TEST:B", Decimal("5"), Decimal("200"), Decimal("190")),
        ),
        Decimal("30000"),
        2,
        now,
        "mock",
    )
    store = MockPortfolioStore((snapshot,))
    summary = PortfolioService(store).summary(WorkspaceAccess(workspace, uuid4(), Role.VIEWER))

    assert summary is not None
    assert summary.equity == Decimal("26960")
    assert summary.unrealized_pnl == Decimal("-30")
    assert summary.gross_exposure == Decimal("1960")
    assert summary.concentration == Decimal("1010") / Decimal("1960")
    assert summary.drawdown == Decimal("3040") / Decimal("30000")
    assert summary.open_orders == 2


def test_portfolio_service_returns_empty_state_when_no_source_is_configured():
    now = datetime.now(UTC)
    workspace = Workspace(uuid4(), "Personal", "personal", None, False, False, now)
    service = PortfolioService(MockPortfolioStore())
    assert service.summary(WorkspaceAccess(workspace, uuid4(), Role.VIEWER)) is None
