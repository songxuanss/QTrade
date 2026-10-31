"""Comparison and leakage-safe time-window rules for backtest experiments."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from qtrade_contracts import BacktestComparison, BacktestComparisonRun
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class WalkForwardWindow:
    """Half-open train and validation intervals with observed sample counts."""

    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    train_samples: int
    validation_samples: int

    def __post_init__(self) -> None:
        if (
            any(
                value.tzinfo is None
                for value in (
                    self.train_start,
                    self.train_end,
                    self.validation_start,
                    self.validation_end,
                )
            )
            or self.train_start >= self.train_end
            or self.train_end != self.validation_start
            or self.validation_start >= self.validation_end
            or type(self.train_samples) is not int
            or self.train_samples < 1
            or type(self.validation_samples) is not int
            or self.validation_samples < 1
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid walk-forward window")


@dataclass(frozen=True)
class WalkForwardPlan:
    windows: tuple[WalkForwardWindow, ...]
    train_window: timedelta
    validation_window: timedelta
    step: timedelta

    def __post_init__(self) -> None:
        if (
            not self.windows
            or self.train_window <= timedelta(0)
            or self.validation_window <= timedelta(0)
            or self.step <= timedelta(0)
            or any(
                left.validation_start >= right.validation_start
                or left.validation_end > right.validation_start
                for left, right in zip(self.windows, self.windows[1:], strict=False)
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid walk-forward plan")


def compare_backtests(
    workspace_id: UUID, runs: Iterable[BacktestComparisonRun]
) -> BacktestComparison:
    """Compare completed runs and call out differences that weaken comparison."""
    rows = tuple(runs)
    if not 2 <= len(rows) <= 20 or len({row.run_id for row in rows}) != len(rows):
        raise PortError(ErrorCode.INVALID_INPUT, "comparison requires 2 to 20 unique runs")
    for row in rows:
        if row.end.astimezone(UTC) <= row.start.astimezone(UTC):
            raise PortError(ErrorCode.INVALID_INPUT, "comparison run has an invalid range")

    fields: dict[str, tuple[str, ...]] = {
        "dataset_id": tuple(repr(row.dataset_id) for row in rows),
        "strategy_version_id": tuple(repr(row.strategy_version_id) for row in rows),
        "time_range": tuple(
            repr((row.start.astimezone(UTC), row.end.astimezone(UTC))) for row in rows
        ),
        "currency": tuple(row.currency.upper() for row in rows),
        "benchmark_id": tuple(repr(row.benchmark_id) for row in rows),
        "execution_config": tuple(row.execution_config_json for row in rows),
    }
    differences = tuple(name for name, values in fields.items() if len(set(values)) > 1)
    warnings = tuple(
        message
        for condition, message in (
            ("dataset_id" in differences, "运行使用了不同数据集，行情样本不具备同基准可比性。"),
            ("time_range" in differences, "运行时间范围不同，收益和风险指标的样本区间不一致。"),
            ("currency" in differences, "运行计价币种不同，金额指标不能直接比较。"),
            ("benchmark_id" in differences, "基准标的不同或缺失，基准收益不能直接比较。"),
        )
        if condition
    )
    return BacktestComparison(
        schema_version=1,
        workspace_id=workspace_id,
        runs=rows,
        comparable=not any(
            name in differences for name in ("dataset_id", "time_range", "currency")
        ),
        warnings=warnings,
        input_differences=differences,
    )


def plan_walk_forward(
    timestamps: tuple[datetime, ...],
    *,
    start: datetime,
    end: datetime,
    train_window: timedelta,
    validation_window: timedelta,
    step: timedelta | None = None,
) -> WalkForwardPlan:
    """Plan rolling train intervals; each validation interval follows its train interval."""
    step = step or validation_window
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start.astimezone(UTC) >= end.astimezone(UTC)
        or train_window <= timedelta(0)
        or validation_window <= timedelta(0)
        or step <= timedelta(0)
        or step < validation_window
        or any(item.tzinfo is None for item in timestamps)
        or any(
            left.astimezone(UTC) >= right.astimezone(UTC)
            for left, right in zip(timestamps, timestamps[1:], strict=False)
        )
        or any(item.astimezone(UTC) < start.astimezone(UTC) for item in timestamps)
        or any(item.astimezone(UTC) >= end.astimezone(UTC) for item in timestamps)
    ):
        raise PortError(ErrorCode.INVALID_INPUT, "invalid walk-forward range or samples")

    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    windows: list[WalkForwardWindow] = []
    validation_start = start_utc + train_window
    while validation_start < end_utc:
        validation_end = min(validation_start + validation_window, end_utc)
        train_samples = sum(
            validation_start - train_window <= item.astimezone(UTC) < validation_start
            for item in timestamps
        )
        validation_samples = sum(
            validation_start <= item.astimezone(UTC) < validation_end for item in timestamps
        )
        if not train_samples or not validation_samples:
            raise PortError(
                ErrorCode.INVALID_INPUT,
                "walk-forward window has insufficient samples",
            )
        windows.append(
            WalkForwardWindow(
                train_start=validation_start - train_window,
                train_end=validation_start,
                validation_start=validation_start,
                validation_end=validation_end,
                train_samples=train_samples,
                validation_samples=validation_samples,
            )
        )
        validation_start += step
    if not windows:
        raise PortError(ErrorCode.INVALID_INPUT, "walk-forward plan has no populated windows")
    return WalkForwardPlan(
        windows=tuple(windows),
        train_window=train_window,
        validation_window=validation_window,
        step=step,
    )


@dataclass(frozen=True)
class WalkForwardOutcome:
    """Completed validation-window result used for aggregation."""

    validation_start: datetime
    validation_end: datetime
    total_return: Decimal
    max_drawdown: Decimal
    trade_count: int

    def __post_init__(self) -> None:
        if (
            self.validation_start.tzinfo is None
            or self.validation_end.tzinfo is None
            or self.validation_start >= self.validation_end
            or not isinstance(self.total_return, Decimal)
            or not isinstance(self.max_drawdown, Decimal)
            or self.max_drawdown < 0
            or type(self.trade_count) is not int
            or self.trade_count < 0
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid walk-forward outcome")


@dataclass(frozen=True)
class WalkForwardSummary:
    """Aggregate of out-of-sample windows; never claims parameter optimality."""

    windows: int
    mean_return: Decimal
    median_return: Decimal
    mean_max_drawdown: Decimal
    worst_max_drawdown: Decimal
    positive_ratio: Decimal
    compounded_return: Decimal
    total_trades: int

    def __post_init__(self) -> None:
        if (
            type(self.windows) is not int
            or self.windows < 1
            or not isinstance(self.compounded_return, Decimal)
            or not Decimal(0) <= self.positive_ratio <= Decimal(1)
            or type(self.total_trades) is not int
            or self.total_trades < 0
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid walk-forward summary")


def summarize_walk_forward(outcomes: Iterable[WalkForwardOutcome]) -> WalkForwardSummary:
    """Summarize ordered validation windows without dropping failed-return outliers."""
    rows = tuple(outcomes)
    if not rows:
        raise PortError(ErrorCode.INVALID_INPUT, "walk-forward summary requires outcomes")
    if any(
        left.validation_start >= right.validation_start
        for left, right in zip(rows, rows[1:], strict=False)
    ):
        raise PortError(ErrorCode.INVALID_INPUT, "walk-forward outcomes must be ordered")
    ordered = sorted(item.total_return for item in rows)
    compounded = Decimal(1)
    for item in rows:
        compounded *= Decimal(1) + item.total_return
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
    )
    return WalkForwardSummary(
        windows=len(rows),
        mean_return=sum((item.total_return for item in rows), Decimal(0)) / Decimal(len(rows)),
        median_return=median,
        mean_max_drawdown=sum((item.max_drawdown for item in rows), Decimal(0))
        / Decimal(len(rows)),
        worst_max_drawdown=max(item.max_drawdown for item in rows),
        positive_ratio=Decimal(sum(item.total_return > 0 for item in rows)) / Decimal(len(rows)),
        compounded_return=compounded - Decimal(1),
        total_trades=sum(item.trade_count for item in rows),
    )


@dataclass(frozen=True)
class ParameterCandidate:
    """One parameter set scored on the in-sample window."""

    parameters_json: str
    total_return: Decimal
    max_drawdown: Decimal

    def __post_init__(self) -> None:
        if (
            not isinstance(self.parameters_json, str)
            or not self.parameters_json
            or not isinstance(self.total_return, Decimal)
            or not isinstance(self.max_drawdown, Decimal)
            or self.max_drawdown < 0
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid parameter candidate")


def choose_parameters(candidates: Iterable[ParameterCandidate]) -> ParameterCandidate:
    """Highest in-sample return wins; ties break on drawdown then parameter JSON."""
    rows = tuple(candidates)
    if not rows:
        raise PortError(ErrorCode.INVALID_INPUT, "parameter selection requires candidates")
    return min(rows, key=lambda item: (-item.total_return, item.max_drawdown, item.parameters_json))
