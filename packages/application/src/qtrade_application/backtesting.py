"""Freeze research inputs before queueing a Backtest Run."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol, cast
from uuid import UUID, uuid4

from qtrade_application.artifacts import ManifestService
from qtrade_application.strategies import StrategyService
from qtrade_contracts import (
    ArtifactManifest,
    BacktestComparison,
    BacktestComparisonRun,
    BacktestResult,
    BacktestRun,
    BacktestWalkForwardAggregate,
    BacktestWalkForwardBatch,
    BacktestWalkForwardPlan,
    BacktestWalkForwardRun,
    BacktestWalkForwardWindow,
    BacktestWalkForwardWindowOutcome,
    BacktestWalkForwardWindowPlan,
    MarketDataset,
)
from qtrade_data_contracts.research import CorporateAction
from qtrade_domain.artifacts import ManifestInputs
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.backtest_execution import BacktestExecutionModel
from qtrade_domain.backtest_experiments import (
    WalkForwardOutcome,
    compare_backtests,
    plan_walk_forward,
    summarize_walk_forward,
)
from qtrade_domain.backtesting import BacktestRunEvent, BacktestRunPort, BacktestRunRecord
from qtrade_domain.datasets import DatasetRepositoryPort
from qtrade_domain.ports import (
    BarQuery,
    ComputeJob,
    ErrorCode,
    MarketDataPort,
    ObjectStorePort,
    PortError,
    TradingEnginePort,
)
from qtrade_domain.strategies import StrategyLifecyclePort, StrategyVersionState


@dataclass(frozen=True, slots=True)
class BacktestEngineIdentity:
    name: str
    version: str
    adapter_version: str
    environment_image_digest: str

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(value, str) or not value or len(value) > 256
                for value in (
                    self.name,
                    self.version,
                    self.adapter_version,
                )
            )
            or not isinstance(self.environment_image_digest, str)
            or len(self.environment_image_digest) != 71
            or not self.environment_image_digest.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in self.environment_image_digest[7:])
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest engine identity")


@dataclass(frozen=True, slots=True)
class BacktestSubmission:
    strategy_id: UUID
    strategy_version: int
    dataset_id: UUID
    start: datetime
    end: datetime
    initial_cash: Decimal
    currency: str
    cost_model_version: str
    parameters: dict[str, object]
    random_seed: int = 0
    benchmark_id: str | None = None
    resource_profile: str = "standard.v1"
    execution_model: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.strategy_id, UUID)
            or type(self.strategy_version) is not int
            or self.strategy_version < 1
            or not isinstance(self.dataset_id, UUID)
            or any(
                not isinstance(item, datetime) or item.utcoffset() is None
                for item in (self.start, self.end)
            )
            or self.start >= self.end
            or not isinstance(self.initial_cash, Decimal)
            or not self.initial_cash.is_finite()
            or self.initial_cash <= 0
            or not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isalpha()
            or not isinstance(self.cost_model_version, str)
            or not 1 <= len(self.cost_model_version) <= 128
            or not isinstance(self.parameters, dict)
            or not isinstance(self.resource_profile, str)
            or not 1 <= len(self.resource_profile) <= 128
            or type(self.random_seed) is not int
            or self.random_seed < 0
            or (
                self.benchmark_id is not None
                and (
                    not isinstance(self.benchmark_id, str) or not 1 <= len(self.benchmark_id) <= 128
                )
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest request")
        try:
            json.dumps(self.parameters, allow_nan=False)
            BacktestExecutionModel.from_config(self.execution_model)
        except (TypeError, ValueError):
            raise PortError(
                ErrorCode.INVALID_INPUT, "backtest parameters must be JSON values"
            ) from None


@dataclass(frozen=True, slots=True)
class WalkForwardSubmission:
    strategy_id: UUID
    strategy_version: int
    dataset_id: UUID
    instrument_id: str
    start: datetime
    end: datetime
    train_window: timedelta
    validation_window: timedelta
    step: timedelta
    initial_cash: Decimal
    currency: str
    cost_model_version: str
    parameters: dict[str, object]
    random_seed: int = 0
    benchmark_id: str | None = None
    execution_model: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.instrument_id, str)
            or not 1 <= len(self.instrument_id) <= 256
            or any(
                item <= timedelta(0)
                for item in (self.train_window, self.validation_window, self.step)
            )
            or self.step < self.validation_window
            or type(self.random_seed) is not int
            or self.random_seed < 0
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid walk-forward request")


class WalkForwardStorePort(Protocol):
    """Durable batch of window runs; outcomes are re-derived, never mutated."""

    def create(self, batch: BacktestWalkForwardBatch) -> None: ...

    def get(self, workspace_id: UUID, walk_forward_id: UUID) -> BacktestWalkForwardBatch: ...


class CorporateActionPort(Protocol):
    """Load only published corporate actions for the frozen dataset scope."""

    def load(
        self,
        access: WorkspaceAccess,
        instrument_ids: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> tuple[CorporateAction, ...]: ...


class BacktestService:
    def __init__(
        self,
        runs: BacktestRunPort,
        strategies: StrategyService,
        lifecycle: StrategyLifecyclePort,
        datasets: DatasetRepositoryPort,
        manifests: ManifestService,
        engine: TradingEnginePort,
        objects: ObjectStorePort,
        identity: BacktestEngineIdentity,
        market_data: MarketDataPort | None = None,
        corporate_actions: CorporateActionPort | None = None,
        walk_forwards: WalkForwardStorePort | None = None,
    ) -> None:
        self._runs = runs
        self._strategies = strategies
        self._lifecycle = lifecycle
        self._datasets = datasets
        self._manifests = manifests
        self._engine = engine
        self._objects = objects
        self._identity = identity
        self._market_data = market_data
        self._corporate_actions = corporate_actions
        self._walk_forwards = walk_forwards

    def create(self, access: WorkspaceAccess, request: BacktestSubmission) -> BacktestRunRecord:
        access.require(Permission.BACKTEST_RUN)
        versions = self._strategies.versions(access, request.strategy_id)
        strategy_version = next(
            (item for item in versions if item.version == request.strategy_version), None
        )
        if strategy_version is None:
            raise PortError(ErrorCode.NOT_FOUND, "strategy version unavailable")
        lifecycle = self._lifecycle.state(
            access.workspace.workspace_id, request.strategy_id, request.strategy_version
        )
        if lifecycle.state in {
            StrategyVersionState.DRAFT,
            StrategyVersionState.FAILED,
            StrategyVersionState.ARCHIVED,
        }:
            raise PortError(ErrorCode.REJECTED, "strategy version must pass static validation")
        dataset = self._datasets.get(access.workspace.workspace_id, request.dataset_id).metadata
        if request.start < dataset.start or request.end > dataset.end:
            raise PortError(ErrorCode.INVALID_INPUT, "backtest range must stay within the dataset")
        if dataset.granularity == "tick":
            raise PortError(
                ErrorCode.INVALID_INPUT, "tick datasets are not supported by event replay"
            )
        if request.benchmark_id is not None and request.benchmark_id not in dataset.instrument_ids:
            raise PortError(
                ErrorCode.INVALID_INPUT, "benchmark must be present in the selected dataset"
            )
        actions = (
            self._corporate_actions.load(access, dataset.instrument_ids, request.start, request.end)
            if self._corporate_actions is not None
            else ()
        )
        resolved_parameters = self._strategies.validate_parameters(
            access, request.strategy_id, request.strategy_version, request.parameters
        )
        now = datetime.now(UTC)
        run_id = uuid4()
        execution_model = BacktestExecutionModel.from_config(request.execution_model)
        execution_config = json.dumps(
            {
                "benchmark_id": request.benchmark_id,
                "cost_model_version": request.cost_model_version,
                "currency": request.currency.upper(),
                "end": request.end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "initial_cash": format(request.initial_cash, "f"),
                "corporate_actions": [
                    {
                        **item.model_dump(mode="json"),
                        "content_version": item.content_version,
                    }
                    for item in actions
                ],
                "execution_model": execution_model.to_config(),
                "resource_profile": request.resource_profile,
                "start": request.start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        manifest, _ = self._manifests.create(
            ManifestInputs(
                run_id=run_id,
                kind="backtest",
                created_by=access.user_id,
                created_at=now,
                strategy=strategy_version,
                dataset=dataset,
                parameters_json=json.dumps(
                    resolved_parameters, sort_keys=True, separators=(",", ":"), allow_nan=False
                ),
                engine_name=self._identity.name,
                engine_version=self._identity.version,
                adapter_version=self._identity.adapter_version,
                environment_image_digest=self._identity.environment_image_digest,
                random_seed=request.random_seed,
                execution_config_json=execution_config,
            )
        )
        run = BacktestRun.model_validate(
            {
                "schema_version": 1,
                "workspace_id": access.workspace.workspace_id,
                "run_id": run_id,
                "strategy_version_id": strategy_version.strategy_version_id,
                "dataset_id": dataset.dataset_id,
                "manifest_id": manifest.manifest_id,
                "created_by": access.user_id,
                "created_at": now,
                "start": request.start,
                "end": request.end,
                "initial_cash": request.initial_cash,
                "currency": request.currency.upper(),
                "benchmark_id": request.benchmark_id,
                "cost_model_version": request.cost_model_version,
                "status": "queued",
                "progress": 0,
            }
        )
        record = BacktestRunRecord(run, None, 1)
        self._runs.create(record, self._event(run))
        try:
            job = self._engine.submit(run, manifest)
        except PortError as error:
            return self._update_job(
                record, ComputeJob(run.workspace_id, run.run_id, "failed", 0, error_code=error.code)
            )
        except Exception:
            return self._update_job(
                record,
                ComputeJob(
                    run.workspace_id,
                    run.run_id,
                    "failed",
                    0,
                    error_code=ErrorCode.UNAVAILABLE,
                ),
            )
        attached = BacktestRunRecord(run, job.job_id, 2)
        try:
            record = self._runs.update(1, attached, None)
        except PortError as error:
            # A durable worker may claim the queued row before the API attaches
            # the submission job. The claim is authoritative and must not fail
            # the request; the engine job id is only an in-memory convenience.
            if error.code != ErrorCode.CONFLICT:
                raise
            current = self._runs.get(run.workspace_id, run.run_id)
            if current.run.run_id != run.run_id or current.revision < 2:
                raise
            return current
        return self._apply_job(record, job)

    def get(self, access: WorkspaceAccess, run_id: UUID) -> BacktestRunRecord:
        access.require(Permission.READ)
        record = self._runs.get(access.workspace.workspace_id, run_id)
        if record.run.status in {"cancelled", "failed", "completed"}:
            return record
        if record.engine_job_id is None:
            return record
        try:
            job = self._engine.status(access.workspace.workspace_id, record.engine_job_id)
        except PortError as error:
            # Durable queue workers own status; an in-memory engine has no job.
            if error.code == ErrorCode.NOT_FOUND:
                return record
            raise
        return self._apply_job(record, job)

    def report(self, access: WorkspaceAccess, run_id: UUID) -> BacktestResult:
        """Read and verify the immutable normalized result for a completed run."""
        record = self.get(access, run_id)
        run = record.run
        if run.status != "completed" or run.result_object_key is None:
            raise PortError(ErrorCode.CONFLICT, "backtest report is not ready")
        stored = self._objects.stat(run.workspace_id, run.result_object_key)
        raw = self._objects.get(
            run.workspace_id, run.result_object_key, expected_sha256=stored.content_sha256
        )
        try:
            result = BacktestResult.model_validate_json(raw)
        except ValueError:
            raise PortError(
                ErrorCode.CHECKSUM_MISMATCH, "stored backtest report is invalid"
            ) from None
        if (
            result.workspace_id != run.workspace_id
            or result.dataset_id != run.dataset_id
            or result.strategy_version_id != run.strategy_version_id
            or result.currency != run.currency
        ):
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "stored report does not match its run")
        if (
            not result.equity_curve
            or result.summary.sample_count != len(result.equity_curve)
            or result.summary.final_equity != result.equity_curve[-1].equity
        ):
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "stored report summary is inconsistent")
        manifest = self._manifests.get(run.workspace_id, run_id)
        if (
            manifest.manifest_id != run.manifest_id
            or result.determinism.seed != manifest.random_seed
        ):
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "stored report manifest is inconsistent")
        return result

    def manifest(self, access: WorkspaceAccess, run_id: UUID) -> ArtifactManifest:
        record = self.get(access, run_id)
        manifest = self._manifests.get(record.run.workspace_id, run_id)
        if manifest.manifest_id != record.run.manifest_id:
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "manifest does not match its run")
        return manifest

    def compare(self, access: WorkspaceAccess, run_ids: tuple[UUID, ...]) -> BacktestComparison:
        """Load workspace-scoped completed runs and compare verified artifacts."""
        access.require(Permission.READ)
        if not 2 <= len(run_ids) <= 20 or len(set(run_ids)) != len(run_ids):
            raise PortError(ErrorCode.INVALID_INPUT, "comparison requires 2 to 20 unique runs")
        rows: list[BacktestComparisonRun] = []
        for run_id in run_ids:
            record = self.get(access, run_id)
            if record.run.status != "completed":
                raise PortError(ErrorCode.CONFLICT, "only completed backtests can be compared")
            manifest = self.manifest(access, run_id)
            rows.append(
                BacktestComparisonRun(
                    schema_version=1,
                    workspace_id=access.workspace.workspace_id,
                    run_id=run_id,
                    dataset_id=record.run.dataset_id,
                    strategy_version_id=record.run.strategy_version_id,
                    start=record.run.start,
                    end=record.run.end,
                    currency=record.run.currency,
                    benchmark_id=record.run.benchmark_id,
                    execution_config_json=manifest.execution_config_json or "{}",
                    result=self.report(access, run_id),
                )
            )
        return compare_backtests(access.workspace.workspace_id, tuple(rows))

    def plan_walk_forward(
        self,
        access: WorkspaceAccess,
        dataset_id: UUID,
        instrument_id: str,
        start: datetime,
        end: datetime,
        train_window: timedelta,
        validation_window: timedelta,
        step: timedelta,
    ) -> BacktestWalkForwardPlan:
        """Plan rolling, disjoint windows from authorized dataset observations."""
        access.require(Permission.READ)
        if self._market_data is None:
            raise PortError(ErrorCode.UNAVAILABLE, "market data is unavailable")
        workspace_id = access.workspace.workspace_id
        dataset = self._datasets.get(workspace_id, dataset_id).metadata
        if (
            instrument_id not in dataset.instrument_ids
            or dataset.granularity == "tick"
            or start < dataset.start
            or end > dataset.end
            or start >= end
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid walk-forward dataset range")
        timestamps: list[datetime] = []
        cursor: str | None = None
        while True:
            page = self._market_data.query_bars(
                BarQuery(
                    workspace_id,
                    dataset_id,
                    instrument_id,
                    dataset.granularity,
                    start,
                    end,
                    limit=10_000,
                    cursor=cursor,
                )
            )
            timestamps.extend(bar.timestamp for bar in page.bars)
            if len(timestamps) > 100_000:
                raise PortError(
                    ErrorCode.INVALID_INPUT,
                    "walk-forward planning is limited to 100000 observations",
                )
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        plan = plan_walk_forward(
            tuple(timestamps),
            start=start,
            end=end,
            train_window=train_window,
            validation_window=validation_window,
            step=step,
        )
        return BacktestWalkForwardPlan(
            schema_version=1,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            instrument_id=instrument_id,
            start=start,
            end=end,
            train_window_seconds=int(train_window.total_seconds()),
            validation_window_seconds=int(validation_window.total_seconds()),
            step_seconds=int(step.total_seconds()),
            windows=tuple(
                BacktestWalkForwardWindow(
                    schema_version=1,
                    workspace_id=workspace_id,
                    train_start=window.train_start,
                    train_end=window.train_end,
                    validation_start=window.validation_start,
                    validation_end=window.validation_end,
                    train_samples=window.train_samples,
                    validation_samples=window.validation_samples,
                )
                for window in plan.windows
            ),
        )

    def start_walk_forward(
        self, access: WorkspaceAccess, request: WalkForwardSubmission
    ) -> BacktestWalkForwardRun:
        """Plan disjoint validation windows and queue one real run per window."""
        access.require(Permission.BACKTEST_RUN)
        if self._walk_forwards is None:
            raise PortError(ErrorCode.UNAVAILABLE, "walk-forward execution is unavailable")
        plan = self.plan_walk_forward(
            access,
            request.dataset_id,
            request.instrument_id,
            request.start,
            request.end,
            request.train_window,
            request.validation_window,
            request.step,
        )
        if len(plan.windows) > 60:
            raise PortError(
                ErrorCode.INVALID_INPUT,
                "walk-forward is limited to 60 windows; widen the step or range",
            )
        created_at = datetime.now(UTC)
        windows: list[BacktestWalkForwardWindowPlan] = []
        first_record: BacktestRunRecord | None = None
        for index, window in enumerate(plan.windows, start=1):
            record = self.create(
                access,
                BacktestSubmission(
                    request.strategy_id,
                    request.strategy_version,
                    request.dataset_id,
                    window.validation_start,
                    window.validation_end,
                    request.initial_cash,
                    request.currency,
                    request.cost_model_version,
                    request.parameters,
                    request.random_seed,
                    request.benchmark_id,
                    execution_model=request.execution_model,
                ),
            )
            first_record = first_record or record
            windows.append(
                BacktestWalkForwardWindowPlan(
                    schema_version=1,
                    workspace_id=access.workspace.workspace_id,
                    index=index,
                    train_start=window.train_start,
                    train_end=window.train_end,
                    validation_start=window.validation_start,
                    validation_end=window.validation_end,
                    train_samples=window.train_samples,
                    validation_samples=window.validation_samples,
                    run_id=record.run.run_id,
                )
            )
        if first_record is None:
            raise PortError(ErrorCode.INVALID_INPUT, "walk-forward plan has no windows")
        manifest = self.manifest(access, first_record.run.run_id)
        batch = BacktestWalkForwardBatch(
            schema_version=1,
            workspace_id=access.workspace.workspace_id,
            walk_forward_id=uuid4(),
            created_at=created_at,
            created_by=access.user_id,
            strategy_id=request.strategy_id,
            strategy_version=request.strategy_version,
            strategy_version_id=first_record.run.strategy_version_id,
            dataset_id=request.dataset_id,
            instrument_id=request.instrument_id,
            start=request.start,
            end=request.end,
            initial_cash=request.initial_cash,
            currency=request.currency.upper(),
            benchmark_id=request.benchmark_id,
            cost_model_version=request.cost_model_version,
            parameters_json=manifest.parameters_json,
            execution_config_json=manifest.execution_config_json or "{}",
            random_seed=request.random_seed,
            train_window_seconds=int(request.train_window.total_seconds()),
            validation_window_seconds=int(request.validation_window.total_seconds()),
            step_seconds=int(request.step.total_seconds()),
            windows=tuple(windows),
        )
        self._walk_forwards.create(batch)
        return self.walk_forward(access, batch.walk_forward_id)

    def walk_forward(
        self, access: WorkspaceAccess, walk_forward_id: UUID
    ) -> BacktestWalkForwardRun:
        """Derive window outcomes and aggregate from the durable run rows."""
        access.require(Permission.READ)
        if self._walk_forwards is None:
            raise PortError(ErrorCode.UNAVAILABLE, "walk-forward execution is unavailable")
        workspace_id = access.workspace.workspace_id
        batch = self._walk_forwards.get(workspace_id, walk_forward_id)
        outcomes: list[WalkForwardOutcome] = []
        windows: list[BacktestWalkForwardWindowOutcome] = []
        for window in batch.windows:
            record = self._runs.get(workspace_id, window.run_id)
            status = record.run.status
            result = self.report(access, window.run_id) if status == "completed" else None
            if result is not None and result.metrics is None:
                raise PortError(ErrorCode.CHECKSUM_MISMATCH, "completed window has no metrics")
            metrics = result.metrics if result is not None else None
            if result is not None and metrics is not None:
                outcomes.append(
                    WalkForwardOutcome(
                        validation_start=window.validation_start,
                        validation_end=window.validation_end,
                        total_return=result.summary.total_return,
                        max_drawdown=metrics.max_drawdown,
                        trade_count=metrics.trade_count,
                    )
                )
            windows.append(
                BacktestWalkForwardWindowOutcome(
                    schema_version=1,
                    workspace_id=workspace_id,
                    index=window.index,
                    train_start=window.train_start,
                    train_end=window.train_end,
                    validation_start=window.validation_start,
                    validation_end=window.validation_end,
                    train_samples=window.train_samples,
                    validation_samples=window.validation_samples,
                    run_id=window.run_id,
                    status=status,
                    total_return=result.summary.total_return if result else None,
                    max_drawdown=metrics.max_drawdown if metrics else None,
                    sharpe_ratio=metrics.sharpe_ratio if metrics else None,
                    trade_count=metrics.trade_count if metrics else None,
                    error_code=record.run.error_code,
                )
            )
        aggregate = None
        if outcomes:
            summary = summarize_walk_forward(tuple(outcomes))
            aggregate = BacktestWalkForwardAggregate(
                schema_version=1,
                workspace_id=workspace_id,
                completed_windows=summary.windows,
                failed_windows=sum(item.status in {"failed", "cancelled"} for item in windows),
                mean_validation_return=summary.mean_return,
                median_validation_return=summary.median_return,
                mean_max_drawdown=summary.mean_max_drawdown,
                worst_max_drawdown=summary.worst_max_drawdown,
                positive_window_ratio=summary.positive_ratio,
                compounded_validation_return=summary.compounded_return,
                total_trade_count=summary.total_trades,
            )
        return BacktestWalkForwardRun(
            schema_version=1,
            workspace_id=workspace_id,
            walk_forward_id=walk_forward_id,
            created_at=batch.created_at,
            strategy_id=batch.strategy_id,
            strategy_version=batch.strategy_version,
            dataset_id=batch.dataset_id,
            instrument_id=batch.instrument_id,
            start=batch.start,
            end=batch.end,
            initial_cash=batch.initial_cash,
            currency=batch.currency,
            benchmark_id=batch.benchmark_id,
            cost_model_version=batch.cost_model_version,
            parameters_json=batch.parameters_json,
            execution_config_json=batch.execution_config_json,
            random_seed=batch.random_seed,
            train_window_seconds=batch.train_window_seconds,
            validation_window_seconds=batch.validation_window_seconds,
            step_seconds=batch.step_seconds,
            status=(
                "running"
                if any(item.status == "running" for item in windows)
                else "queued"
                if any(item.status in {"queued", "running"} for item in windows)
                else "completed"
                if all(item.status == "completed" for item in windows)
                else "partial"
                if any(item.status == "completed" for item in windows)
                else "failed"
            ),
            windows=tuple(windows),
            aggregate=aggregate,
        )

    def list(self, access: WorkspaceAccess, limit: int = 100) -> tuple[BacktestRunRecord, ...]:
        access.require(Permission.READ)
        records = self._runs.list(access.workspace.workspace_id, limit)
        reconciled: list[BacktestRunRecord] = []
        for record in records:
            if (
                record.run.status in {"cancelled", "failed", "completed"}
                or record.engine_job_id is None
            ):
                reconciled.append(record)
                continue
            try:
                job = self._engine.status(access.workspace.workspace_id, record.engine_job_id)
            except PortError as error:
                # A lost in-memory job must not hide an otherwise readable run.
                if error.code == ErrorCode.NOT_FOUND:
                    reconciled.append(record)
                    continue
                raise
            reconciled.append(self._apply_job(record, job))
        return tuple(reconciled)

    def datasets(self, access: WorkspaceAccess, limit: int = 100) -> tuple[MarketDataset, ...]:
        access.require(Permission.READ)
        return tuple(
            item.metadata for item in self._datasets.list(access.workspace.workspace_id, limit)
        )

    def cancel(self, access: WorkspaceAccess, run_id: UUID) -> BacktestRunRecord:
        access.require(Permission.BACKTEST_RUN)
        record = self._runs.get(access.workspace.workspace_id, run_id)
        if record.run.status in {"cancelled", "failed", "completed"}:
            raise PortError(ErrorCode.CONFLICT, "backtest run is already terminal")
        request_cancel = getattr(self._runs, "request_cancel", None)
        if request_cancel is not None:
            return cast(
                BacktestRunRecord,
                request_cancel(access.workspace.workspace_id, run_id, datetime.now(UTC)),
            )
        if record.engine_job_id is None:
            raise PortError(ErrorCode.CONFLICT, "backtest submission is still being accepted")
        job = self._engine.cancel(access.workspace.workspace_id, record.engine_job_id)
        return self._apply_job(record, job)

    def history(self, access: WorkspaceAccess, run_id: UUID) -> tuple[BacktestRunEvent, ...]:
        access.require(Permission.READ)
        self._runs.get(access.workspace.workspace_id, run_id)
        return self._runs.history(access.workspace.workspace_id, run_id)

    def _apply_job(self, record: BacktestRunRecord, job: ComputeJob) -> BacktestRunRecord:
        run = record.run
        if job.workspace_id != run.workspace_id or job.job_id != record.engine_job_id:
            raise PortError(ErrorCode.UNAVAILABLE, "engine returned a mismatched backtest job")
        key = job.result.key if job.status == "completed" and job.result is not None else None
        error = job.error_code.value if job.error_code is not None else None
        if job.status == "completed":
            if job.result is None or job.result.workspace_id != run.workspace_id:
                raise PortError(ErrorCode.UNAVAILABLE, "completed job has no scoped result")
            stored = self._objects.stat(run.workspace_id, job.result.key)
            if stored != job.result:
                raise PortError(ErrorCode.CHECKSUM_MISMATCH, "backtest result reference changed")
        if job.status == "failed" and error is None:
            error = "engine_error"
        updated_run = BacktestRun.model_validate(
            {
                **run.model_dump(),
                "status": job.status,
                "progress": 100 if job.status == "completed" else job.progress,
                "result_object_key": key,
                "error_code": error,
            }
        )
        if updated_run == run:
            return record
        return self._update_job(record, job, updated_run)

    def _update_job(
        self,
        record: BacktestRunRecord,
        job: ComputeJob,
        updated_run: BacktestRun | None = None,
    ) -> BacktestRunRecord:
        if updated_run is None:
            updated_run = BacktestRun.model_validate(
                {
                    **record.run.model_dump(),
                    "status": "failed",
                    "error_code": job.error_code.value if job.error_code else "engine_error",
                }
            )
        event = self._event(updated_run, record.revision + 1)
        return self._runs.update(
            record.revision,
            BacktestRunRecord(updated_run, job.job_id, record.revision + 1),
            event,
        )

    @staticmethod
    def _event(run: BacktestRun, revision: int = 1) -> BacktestRunEvent:
        return BacktestRunEvent(
            uuid4(),
            run.workspace_id,
            run.run_id,
            run.status,
            run.progress,
            datetime.now(UTC),
            run.error_code,
            revision,
        )
