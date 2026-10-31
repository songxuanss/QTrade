import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from qtrade_contracts import BacktestComparisonRun, BacktestResult
from qtrade_domain.backtest_experiments import (
    compare_backtests,
    plan_walk_forward,
)
from qtrade_domain.ports import PortError

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


def result() -> BacktestResult:
    raw = json.loads((FIXTURES / "BacktestResult.json").read_text())
    return BacktestResult.model_validate_json(json.dumps(raw))


def comparison_input(*, dataset_id: UUID, run_id: UUID) -> BacktestComparisonRun:
    base = result()
    return BacktestComparisonRun(
        schema_version=1,
        workspace_id=UUID(int=8),
        run_id=run_id,
        dataset_id=dataset_id,
        strategy_version_id=base.strategy_version_id,
        start=datetime(2026, 10, 1, tzinfo=UTC),
        end=datetime(2026, 10, 2, tzinfo=UTC),
        currency="USD",
        benchmark_id="SSE:000300",
        execution_config_json='{"fee_bps":"3"}',
        result=base.model_copy(
            update={
                "workspace_id": UUID(int=8),
                "dataset_id": dataset_id,
            }
        ),
    )


def test_comparison_reports_input_differences_and_incomparability():
    first_id, second_id = UUID(int=1), UUID(int=2)
    first = comparison_input(dataset_id=UUID(int=3), run_id=first_id)
    second = comparison_input(dataset_id=UUID(int=4), run_id=second_id)

    comparison = compare_backtests(UUID(int=8), (first, second))

    assert not comparison.comparable
    assert comparison.input_differences == ("dataset_id",)
    assert "不同数据集" in comparison.warnings[0]


def test_comparison_requires_unique_runs():
    item = comparison_input(dataset_id=UUID(int=3), run_id=UUID(int=1))
    with pytest.raises(PortError):
        compare_backtests(UUID(int=8), (item, item))


def test_walk_forward_windows_are_half_open_non_leaking_and_count_samples():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    samples = tuple(start + timedelta(days=day) for day in range(8))

    plan = plan_walk_forward(
        samples,
        start=start,
        end=start + timedelta(days=8),
        train_window=timedelta(days=3),
        validation_window=timedelta(days=2),
    )

    assert len(plan.windows) == 3
    first = plan.windows[0]
    assert first.train_start == start
    assert first.train_end == first.validation_start == start + timedelta(days=3)
    assert first.validation_end == start + timedelta(days=5)
    assert first.train_samples == 3
    assert first.validation_samples == 2
    assert plan.windows[1].validation_start == first.validation_end


def test_walk_forward_rejects_unsorted_or_unpopulated_inputs():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(PortError):
        plan_walk_forward(
            (start + timedelta(days=1), start),
            start=start,
            end=start + timedelta(days=4),
            train_window=timedelta(days=1),
            validation_window=timedelta(days=1),
        )
    with pytest.raises(PortError):
        plan_walk_forward(
            (start,),
            start=start,
            end=start + timedelta(days=4),
            train_window=timedelta(days=1),
            validation_window=timedelta(days=1),
        )


def test_summarize_walk_forward_reports_return_and_risk_aggregates():
    from decimal import Decimal

    from qtrade_domain.backtest_experiments import (
        WalkForwardOutcome,
        summarize_walk_forward,
    )

    start = datetime(2026, 1, 1, tzinfo=UTC)
    outcomes = (
        WalkForwardOutcome(
            start,
            start + timedelta(days=1),
            Decimal("0.10"),
            Decimal("0.02"),
            2,
        ),
        WalkForwardOutcome(
            start + timedelta(days=1),
            start + timedelta(days=2),
            Decimal("-0.05"),
            Decimal("0.06"),
            4,
        ),
        WalkForwardOutcome(
            start + timedelta(days=2),
            start + timedelta(days=3),
            Decimal("0.03"),
            Decimal("0.01"),
            3,
        ),
    )

    summary = summarize_walk_forward(outcomes)

    assert summary.windows == 3
    assert summary.mean_return == Decimal("0.08") / Decimal(3)
    assert summary.median_return == Decimal("0.03")
    assert summary.mean_max_drawdown == Decimal("0.09") / Decimal(3)
    assert summary.worst_max_drawdown == Decimal("0.06")
    assert summary.positive_ratio == Decimal(2) / Decimal(3)
    assert summary.compounded_return == Decimal("1.1") * Decimal("0.95") * Decimal("1.03") - 1
    assert summary.total_trades == 9


def test_summarize_walk_forward_rejects_empty_or_unordered_windows():
    from decimal import Decimal

    from qtrade_domain.backtest_experiments import (
        WalkForwardOutcome,
        summarize_walk_forward,
    )

    start = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(PortError):
        summarize_walk_forward(())
    later = WalkForwardOutcome(
        start + timedelta(days=1),
        start + timedelta(days=2),
        Decimal("0.01"),
        Decimal("0.01"),
        1,
    )
    earlier = WalkForwardOutcome(
        start,
        start + timedelta(days=1),
        Decimal("0.01"),
        Decimal("0.01"),
        1,
    )
    with pytest.raises(PortError):
        summarize_walk_forward((later, earlier))


def test_choose_parameters_prefers_return_then_drawdown_then_name():
    from decimal import Decimal

    from qtrade_domain.backtest_experiments import ParameterCandidate, choose_parameters

    candidates = (
        ParameterCandidate('{"fast":5}', Decimal("0.10"), Decimal("0.05")),
        ParameterCandidate('{"fast":2}', Decimal("0.10"), Decimal("0.02")),
        ParameterCandidate('{"fast":9}', Decimal("0.08"), Decimal("0.01")),
    )
    assert choose_parameters(candidates).parameters_json == '{"fast":2}'

    tied = (
        ParameterCandidate('{"fast":5}', Decimal("0.10"), Decimal("0.02")),
        ParameterCandidate('{"fast":2}', Decimal("0.10"), Decimal("0.02")),
    )
    assert choose_parameters(tied).parameters_json == '{"fast":2}'
    with pytest.raises(PortError):
        choose_parameters(())
