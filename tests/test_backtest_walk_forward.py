"""Walk-forward batches queue one real backtest per validation window."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from qtrade_application.artifacts import ManifestService
from qtrade_application.backtesting import (
    BacktestEngineIdentity,
    BacktestService,
    WalkForwardSubmission,
)
from qtrade_application.strategies import StrategyService
from qtrade_contracts import (
    BacktestWalkForwardAggregate,
    BacktestWalkForwardBatch,
    BacktestWalkForwardRun,
    BacktestWalkForwardWindowOutcome,
    MarketDataset,
    StrategyVersion,
)
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.backtesting import BacktestRunEvent, BacktestRunRecord
from qtrade_domain.datasets import PublishedDataset
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_domain.strategies import (
    Strategy,
    StrategyStateTransition,
    StrategyVersionLifecycle,
    StrategyVersionState,
)
from qtrade_mock.backtesting import MockBacktestRunStore
from qtrade_mock.datasets import MockDatasetRepository
from qtrade_mock.ports import MockMarketData, MockObjectStore, MockTradingEngine
from qtrade_mock.strategies import MockStrategyStore

FIXTURES = Path(__file__).parent / "fixtures/contracts"


class MemoryWalkForwardStore:
    def __init__(self) -> None:
        self.batches: dict[tuple[UUID, UUID], BacktestWalkForwardBatch] = {}

    def create(self, batch: BacktestWalkForwardBatch) -> None:
        key = (batch.workspace_id, batch.walk_forward_id)
        if key in self.batches:
            raise PortError(ErrorCode.CONFLICT, "walk-forward batch already exists")
        self.batches[key] = batch

    def get(self, workspace_id: UUID, walk_forward_id: UUID) -> BacktestWalkForwardBatch:
        try:
            return self.batches[(workspace_id, walk_forward_id)]
        except KeyError:
            raise PortError(ErrorCode.NOT_FOUND, "walk-forward batch unavailable") from None


def build_service(market: MockMarketData, run_store=None):
    actor, workspace_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    strategy_id, version_id, dataset_id = uuid4(), uuid4(), uuid4()
    source = "def signal(close):\n    return close[-1]\n"
    strategy_store = MockStrategyStore()
    strategy_store.create(
        Strategy(strategy_id, workspace_id, "Momentum", "", actor, now, now, 1),
        StrategyVersion(
            schema_version=1,
            workspace_id=workspace_id,
            strategy_id=strategy_id,
            strategy_version_id=version_id,
            version=1,
            created_by=actor,
            created_at=now,
            code_object_key=f"strategies/{strategy_id}/v1.py",
            code_sha256=sha256(source.encode()).hexdigest(),
            parameter_schema_json='{"type":"object","properties":{},"required":[]}',
            dependencies_sha256=sha256(b"{}").hexdigest(),
        ),
    )
    strategy_store.transition(
        StrategyVersionState.DRAFT,
        StrategyVersionLifecycle(
            workspace_id, strategy_id, version_id, 1, StrategyVersionState.VALIDATED, now
        ),
        StrategyStateTransition(
            uuid4(),
            workspace_id,
            strategy_id,
            version_id,
            1,
            StrategyVersionState.DRAFT,
            StrategyVersionState.VALIDATED,
            actor,
            now,
        ),
    )
    metadata = MarketDataset.model_validate_json(
        (FIXTURES / "MarketDataset.json").read_text()
    ).model_copy(
        update={
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "instrument_ids": ("MOCK:ABC",),
            "created_at": now,
        }
    )
    datasets = MockDatasetRepository()
    datasets.put(actor, PublishedDataset(metadata, "b" * 64, None, "c" * 64))
    objects = MockObjectStore()
    runs = run_store or MockBacktestRunStore()
    batches = MemoryWalkForwardStore()
    service = BacktestService(
        runs,
        StrategyService(strategy_store, objects),
        strategy_store,
        datasets,
        ManifestService(objects),
        MockTradingEngine(),
        objects,
        BacktestEngineIdentity("mock", "1", "1", "sha256:" + "a" * 64),
        market_data=market,
        walk_forwards=batches,
    )
    workspace = Workspace(workspace_id, "Research", "team", actor, False, False, now)
    access = WorkspaceAccess(workspace, actor, Role.ADMIN)
    return service, access, strategy_id, dataset_id, runs, batches


def write_hourly_bars(market: MockMarketData, workspace_id: UUID, dataset_id: UUID) -> None:
    from qtrade_contracts import Bar

    template = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    base = datetime(2026, 10, 1, tzinfo=UTC)
    market.write_bars(
        workspace_id,
        dataset_id,
        tuple(
            template.model_copy(
                update={
                    "workspace_id": workspace_id,
                    "dataset_id": dataset_id,
                    "timestamp": base + timedelta(hours=hour),
                }
            )
            for hour in range(24)
        ),
    )


def test_start_walk_forward_queues_one_run_per_validation_window():
    market = MockMarketData()
    service, access, strategy_id, dataset_id, runs, batches = build_service(market)
    write_hourly_bars(market, access.workspace.workspace_id, dataset_id)
    start = datetime(2026, 10, 1, tzinfo=UTC)

    result = service.start_walk_forward(
        access,
        WalkForwardSubmission(
            strategy_id,
            1,
            dataset_id,
            "MOCK:ABC",
            start,
            start + timedelta(days=1),
            timedelta(hours=6),
            timedelta(hours=2),
            timedelta(hours=2),
            Decimal("10000"),
            "USD",
            "cost.v1",
            {},
        ),
    )

    assert result.status == "queued"
    assert len(result.windows) == 9
    assert result.aggregate is None
    assert result.parameters_json == "{}"
    assert json.loads(result.execution_config_json)["initial_cash"] == "10000"
    assert [item.index for item in result.windows] == list(range(1, 10))
    assert result.windows[0].validation_start == start + timedelta(hours=6)
    for window in result.windows:
        record = runs.get(access.workspace.workspace_id, window.run_id)
        assert record.run.status == "queued"
        assert record.run.start == window.validation_start
        assert record.run.end == window.validation_end
    stored = batches.get(access.workspace.workspace_id, result.walk_forward_id)
    assert len(stored.windows) == 9
    refreshed = service.walk_forward(access, result.walk_forward_id)
    assert refreshed.windows == result.windows


def test_walk_forward_rolls_up_failed_windows_without_inventing_metrics():
    market = MockMarketData()
    service, access, strategy_id, dataset_id, runs, _ = build_service(market)
    write_hourly_bars(market, access.workspace.workspace_id, dataset_id)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    result = service.start_walk_forward(
        access,
        WalkForwardSubmission(
            strategy_id,
            1,
            dataset_id,
            "MOCK:ABC",
            start,
            start + timedelta(hours=12),
            timedelta(hours=4),
            timedelta(hours=2),
            timedelta(hours=2),
            Decimal("10000"),
            "USD",
            "cost.v1",
            {},
        ),
    )
    assert len(result.windows) == 4

    first = result.windows[0]
    record = runs.get(access.workspace.workspace_id, first.run_id)
    failed_run = record.run.model_copy(update={"status": "failed", "error_code": "engine_error"})
    runs.update(
        record.revision,
        BacktestRunRecord(failed_run, record.engine_job_id, record.revision + 1),
        BacktestRunEvent(
            uuid4(),
            access.workspace.workspace_id,
            first.run_id,
            "failed",
            record.run.progress,
            datetime.now(UTC),
            "engine_error",
            record.revision + 1,
        ),
    )

    refreshed = service.walk_forward(access, result.walk_forward_id)

    assert refreshed.status == "queued"
    assert refreshed.windows[0].status == "failed"
    assert refreshed.windows[0].total_return is None
    assert refreshed.windows[0].error_code == "engine_error"


def test_walk_forward_requires_permission_and_valid_windows():
    market = MockMarketData()
    service, access, strategy_id, dataset_id, _, _ = build_service(market)
    write_hourly_bars(market, access.workspace.workspace_id, dataset_id)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    submission = WalkForwardSubmission(
        strategy_id,
        1,
        dataset_id,
        "MOCK:ABC",
        start,
        start + timedelta(days=1),
        timedelta(hours=6),
        timedelta(hours=2),
        timedelta(hours=2),
        Decimal("10000"),
        "USD",
        "cost.v1",
        {},
    )
    viewer = WorkspaceAccess(access.workspace, access.user_id, Role.VIEWER)
    with pytest.raises(PortError) as denied:
        service.start_walk_forward(viewer, submission)
    assert denied.value.code == ErrorCode.REJECTED
    with pytest.raises(PortError):
        WalkForwardSubmission(
            strategy_id,
            1,
            dataset_id,
            "MOCK:ABC",
            start,
            start + timedelta(days=1),
            timedelta(hours=6),
            timedelta(hours=2),
            timedelta(hours=1),
            Decimal("10000"),
            "USD",
            "cost.v1",
            {},
        )


def _window_outcome(index: int, status: str, **overrides):
    start = datetime(2026, 10, 1, tzinfo=UTC)
    values = dict(
        schema_version=1,
        workspace_id=UUID(int=8),
        index=index,
        train_start=start + timedelta(days=index - 1),
        train_end=start + timedelta(days=index),
        validation_start=start + timedelta(days=index),
        validation_end=start + timedelta(days=index + 1),
        train_samples=5,
        validation_samples=5,
        run_id=uuid4(),
        status=status,
    )
    if status == "completed":
        values.update(total_return="0.01", max_drawdown="0.02", trade_count=1)
    values.update(overrides)
    return BacktestWalkForwardWindowOutcome(**values)


def test_walk_forward_contract_requires_rollup_to_match_windows():
    base = dict(
        schema_version=1,
        workspace_id=UUID(int=8),
        walk_forward_id=uuid4(),
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
        strategy_id=uuid4(),
        strategy_version=1,
        dataset_id=uuid4(),
        instrument_id="MOCK:ABC",
        start=datetime(2026, 10, 1, tzinfo=UTC),
        end=datetime(2026, 10, 9, tzinfo=UTC),
        initial_cash="10000",
        currency="USD",
        cost_model_version="cost.v1",
        parameters_json="{}",
        execution_config_json="{}",
        random_seed=0,
        train_window_seconds=86_400,
        validation_window_seconds=86_400,
        step_seconds=86_400,
    )
    aggregate = BacktestWalkForwardAggregate(
        schema_version=1,
        workspace_id=UUID(int=8),
        completed_windows=2,
        failed_windows=0,
        mean_validation_return="0.01",
        median_validation_return="0.01",
        mean_max_drawdown="0.02",
        worst_max_drawdown="0.02",
        positive_window_ratio="1",
        compounded_validation_return="0.0201",
        total_trade_count=2,
    )
    completed = BacktestWalkForwardRun(
        **base,
        status="completed",
        windows=(_window_outcome(1, "completed"), _window_outcome(2, "completed")),
        aggregate=aggregate,
    )
    assert completed.status == "completed"
    with pytest.raises(ValueError):
        BacktestWalkForwardRun(
            **base,
            status="completed",
            windows=(
                _window_outcome(1, "completed"),
                _window_outcome(2, "failed"),
            ),
            aggregate=aggregate,
        )
    with pytest.raises(ValueError):
        BacktestWalkForwardWindowOutcome(
            schema_version=1,
            workspace_id=UUID(int=8),
            index=1,
            train_start=datetime(2026, 10, 1, tzinfo=UTC),
            train_end=datetime(2026, 10, 2, tzinfo=UTC),
            validation_start=datetime(2026, 10, 2, tzinfo=UTC),
            validation_end=datetime(2026, 10, 3, tzinfo=UTC),
            train_samples=1,
            validation_samples=1,
            run_id=uuid4(),
            status="completed",
        )


class ClaimingStore(MockBacktestRunStore):
    """A durable worker claims the queued run before the API attaches its job."""

    def __init__(self) -> None:
        super().__init__()
        self.claimed = False

    def update(self, expected_revision, record, event):
        if not self.claimed:
            self.claimed = True
            current = self.get(record.run.workspace_id, record.run.run_id)
            running = current.run.model_copy(update={"status": "running", "progress": 10})
            super().update(
                current.revision,
                BacktestRunRecord(running, current.engine_job_id, current.revision + 1),
                BacktestRunEvent(
                    uuid4(),
                    running.workspace_id,
                    running.run_id,
                    "running",
                    10,
                    datetime.now(UTC),
                    revision=current.revision + 1,
                ),
            )
            raise PortError(ErrorCode.CONFLICT, "backtest run changed")
        return super().update(expected_revision, record, event)


def test_submission_tolerates_a_worker_claiming_before_the_job_attach():
    market = MockMarketData()
    runs = ClaimingStore()
    service, access, strategy_id, dataset_id, _, _ = build_service(market, runs)
    write_hourly_bars(market, access.workspace.workspace_id, dataset_id)
    start = datetime(2026, 10, 1, tzinfo=UTC)

    result = service.start_walk_forward(
        access,
        WalkForwardSubmission(
            strategy_id,
            1,
            dataset_id,
            "MOCK:ABC",
            start,
            start + timedelta(hours=8),
            timedelta(hours=4),
            timedelta(hours=2),
            timedelta(hours=2),
            Decimal("10000"),
            "USD",
            "cost.v1",
            {},
        ),
    )

    assert runs.claimed
    assert len(result.windows) == 2
    first = runs.get(access.workspace.workspace_id, result.windows[0].run_id)
    assert first.run.status == "running"
    assert result.windows[0].status == "running"
    second = runs.get(access.workspace.workspace_id, result.windows[1].run_id)
    assert second.run.status == "queued"
