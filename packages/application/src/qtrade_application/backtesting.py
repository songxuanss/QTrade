"""Freeze research inputs before queueing a Backtest Run."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import product
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from qtrade_application.artifacts import ManifestService
from qtrade_application.backtest_export import (
    ExportArtifact,
    parse_format,
    run_csv,
    run_html,
    walk_forward_csv,
    walk_forward_html,
)
from qtrade_application.strategies import StrategyService
from qtrade_contracts import (
    ArtifactManifest,
    BacktestComparison,
    BacktestComparisonRun,
    BacktestResult,
    BacktestRun,
    BacktestUniverse,
    BacktestUniverseMembership,
    BacktestWalkForwardAggregate,
    BacktestWalkForwardBatch,
    BacktestWalkForwardCandidate,
    BacktestWalkForwardPlan,
    BacktestWalkForwardRun,
    BacktestWalkForwardWindow,
    BacktestWalkForwardWindowOutcome,
    BacktestWalkForwardWindowPlan,
    MarketDataset,
    UniverseMembership,
)
from qtrade_data_contracts.research import AdjustmentFactor, CorporateAction
from qtrade_domain.artifacts import ManifestInputs
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.backtest_execution import BacktestExecutionModel
from qtrade_domain.backtest_experiments import (
    ParameterCandidate,
    WalkForwardOutcome,
    choose_parameters,
    compare_backtests,
    plan_walk_forward,
    summarize_walk_forward,
)
from qtrade_domain.backtesting import BacktestRunEvent, BacktestRunPort, BacktestRunRecord
from qtrade_domain.corporate_actions import validate_adjustment_factors
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
from qtrade_domain.universe import (
    MembershipWindow,
    coverage_for,
    disclosure,
)


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
    universe_mode: Literal["dataset", "membership"] = "dataset"
    rights_policy: Literal["subscribe", "skip"] = "subscribe"
    adjustment_factor_mode: Literal["freeze", "vendor_adjusted"] = "freeze"
    parameter_grid: dict[str, list[object]] | None = None

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
            or self.universe_mode not in {"dataset", "membership"}
            or self.rights_policy not in {"subscribe", "skip"}
            or self.adjustment_factor_mode not in {"freeze", "vendor_adjusted"}
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
    universe_mode: Literal["dataset", "membership"] = "dataset"
    rights_policy: Literal["subscribe", "skip"] = "subscribe"
    adjustment_factor_mode: Literal["freeze", "vendor_adjusted"] = "freeze"
    parameter_grid: dict[str, list[object]] | None = None

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
            or self.universe_mode not in {"dataset", "membership"}
            or self.rights_policy not in {"subscribe", "skip"}
            or self.adjustment_factor_mode not in {"freeze", "vendor_adjusted"}
            or (
                self.parameter_grid is not None
                and (
                    not isinstance(self.parameter_grid, dict)
                    or not self.parameter_grid
                    or len(self.parameter_grid) > 8
                    or any(
                        not isinstance(name, str)
                        or not name
                        or not isinstance(values, list)
                        or not values
                        or len(values) > 20
                        for name, values in self.parameter_grid.items()
                    )
                )
            )
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid walk-forward request")
        if self.parameter_grid is not None:
            try:
                json.dumps(self.parameter_grid, allow_nan=False)
            except (TypeError, ValueError):
                raise PortError(
                    ErrorCode.INVALID_INPUT, "walk-forward grid must be JSON values"
                ) from None


class MembershipQueryPort(Protocol):
    """Load workspace membership intervals for dataset instruments."""

    def for_instruments(
        self, workspace_id: UUID, instrument_ids: tuple[str, ...]
    ) -> tuple[UniverseMembership, ...]: ...


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


class AdjustmentFactorPort(Protocol):
    """Load published vendor adjustment factors for the frozen scope."""

    def load(
        self,
        access: WorkspaceAccess,
        instrument_ids: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> tuple[AdjustmentFactor, ...]: ...


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
        memberships: MembershipQueryPort | None = None,
        adjustment_factors: AdjustmentFactorPort | None = None,
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
        self._memberships = memberships
        self._adjustment_factors = adjustment_factors

    def _universe(
        self,
        access: WorkspaceAccess,
        instrument_ids: tuple[str, ...],
        start: datetime,
        end: datetime,
        mode: str,
    ) -> BacktestUniverse:
        """Resolve and disclose membership coverage before anything is frozen."""
        if mode == "dataset":
            coverage = coverage_for(instrument_ids, (), start, end)
            return BacktestUniverse(
                mode="dataset",
                instruments=coverage.instruments,
                full_coverage=coverage.full_coverage,
                partial_coverage=coverage.partial_coverage,
                unknown=coverage.unknown,
                disclosure=disclosure("dataset", coverage),
            )
        if mode != "membership":
            raise PortError(ErrorCode.INVALID_INPUT, "invalid universe mode")
        if self._memberships is None:
            raise PortError(ErrorCode.UNAVAILABLE, "universe membership data is unavailable")
        records = self._memberships.for_instruments(access.workspace.workspace_id, instrument_ids)
        windows = tuple(
            MembershipWindow(item.instrument_id, item.effective_from, item.effective_to)
            for item in records
        )
        coverage = coverage_for(instrument_ids, windows, start, end)
        if coverage.unknown:
            missing = sorted(set(instrument_ids) - {item.instrument_id for item in records})
            raise PortError(
                ErrorCode.INVALID_INPUT,
                "membership intervals are missing for: " + ", ".join(missing[:8]),
            )
        return BacktestUniverse(
            mode="membership",
            memberships=tuple(
                BacktestUniverseMembership(
                    instrument_id=item.instrument_id,
                    effective_from=item.effective_from,
                    effective_to=item.effective_to,
                    source=item.source,
                    evidence=item.evidence,
                    content_version=item.content_version,
                )
                for item in records
            ),
            instruments=coverage.instruments,
            full_coverage=coverage.full_coverage,
            partial_coverage=coverage.partial_coverage,
            unknown=coverage.unknown,
            disclosure=disclosure("membership", coverage),
        )

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
        if request.benchmark_id is not None and request.benchmark_id not in dataset.instrument_ids:
            raise PortError(
                ErrorCode.INVALID_INPUT, "benchmark must be present in the selected dataset"
            )
        universe = self._universe(
            access, dataset.instrument_ids, request.start, request.end, request.universe_mode
        )
        if request.adjustment_factor_mode == "vendor_adjusted":
            raise PortError(
                ErrorCode.UNSUPPORTED,
                "vendor-adjusted price replay is not implemented; factors can only be "
                "frozen and reconciled against the corporate-action ledger",
            )
        actions = (
            self._corporate_actions.load(access, dataset.instrument_ids, request.start, request.end)
            if self._corporate_actions is not None
            else ()
        )
        factors = validate_adjustment_factors(
            actions,
            (
                self._adjustment_factors.load(
                    access, dataset.instrument_ids, request.start, request.end
                )
                if self._adjustment_factors is not None
                else ()
            ),
            dataset.instrument_ids,
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
                "rights_policy": request.rights_policy,
                "adjustment_factors": [
                    {
                        **item.model_dump(mode="json"),
                        "content_version": item.content_version,
                    }
                    for item in factors
                ],
                "adjustment_factor_status": (
                    "frozen_reconciliation_only" if factors else "vendor_unavailable"
                ),
                "universe": universe.model_dump(mode="json"),
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

    def export_run(
        self, access: WorkspaceAccess, run_id: UUID, export_format: str
    ) -> ExportArtifact:
        """Render a completed run's verified artifacts as a downloadable file."""
        access.require(Permission.READ)
        parse_format(export_format)
        record = self.get(access, run_id)
        result = self.report(access, run_id)
        manifest = self.manifest(access, run_id)
        return (
            run_csv(record.run, result, manifest)
            if export_format == "csv"
            else run_html(record.run, result, manifest)
        )

    def export_walk_forward(
        self, access: WorkspaceAccess, walk_forward_id: UUID, export_format: str
    ) -> ExportArtifact:
        access.require(Permission.READ)
        parse_format(export_format)
        batch = self.walk_forward(access, walk_forward_id)
        return walk_forward_csv(batch) if export_format == "csv" else walk_forward_html(batch)

    def _parameter_combinations(
        self, access: WorkspaceAccess, request: WalkForwardSubmission
    ) -> tuple[dict[str, object], ...]:
        """Expand and validate the frozen parameter grid against the version schema."""
        grid = request.parameter_grid
        if not grid:
            return (
                self._strategies.validate_parameters(
                    access,
                    request.strategy_id,
                    request.strategy_version,
                    request.parameters,
                ),
            )
        names = sorted(grid)
        combinations: list[dict[str, object]] = []
        seen: set[str] = set()
        for values in product(*(grid[name] for name in names)):
            candidate = {**request.parameters, **dict(zip(names, values, strict=True))}
            resolved = self._strategies.validate_parameters(
                access, request.strategy_id, request.strategy_version, candidate
            )
            canonical = json.dumps(resolved, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if canonical not in seen:
                seen.add(canonical)
                combinations.append(resolved)
        if not combinations:
            raise PortError(ErrorCode.INVALID_INPUT, "walk-forward grid is empty")
        return tuple(combinations)

    def _walk_forward_run(
        self,
        access: WorkspaceAccess,
        request: WalkForwardSubmission,
        parameters: dict[str, object],
        start: datetime,
        end: datetime,
    ) -> BacktestRunRecord:
        return self.create(
            access,
            BacktestSubmission(
                request.strategy_id,
                request.strategy_version,
                request.dataset_id,
                start,
                end,
                request.initial_cash,
                request.currency,
                request.cost_model_version,
                parameters,
                request.random_seed,
                request.benchmark_id,
                execution_model=request.execution_model,
                universe_mode=request.universe_mode,
                rights_policy=request.rights_policy,
                adjustment_factor_mode=request.adjustment_factor_mode,
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
        combinations = self._parameter_combinations(access, request)
        if len(combinations) * len(plan.windows) * 2 > 300:
            raise PortError(
                ErrorCode.INVALID_INPUT,
                "walk-forward optimization is limited to 300 runs; reduce grid or windows",
            )
        created_at = datetime.now(UTC)
        windows: list[BacktestWalkForwardWindowPlan] = []
        first_record: BacktestRunRecord | None = None
        for index, window in enumerate(plan.windows, start=1):
            candidates: list[BacktestWalkForwardCandidate] = []
            for parameters in combinations:
                train_record = self._walk_forward_run(
                    access, request, parameters, window.train_start, window.train_end
                )
                validation_record = self._walk_forward_run(
                    access,
                    request,
                    parameters,
                    window.validation_start,
                    window.validation_end,
                )
                first_record = first_record or validation_record
                candidates.append(
                    BacktestWalkForwardCandidate(
                        schema_version=1,
                        workspace_id=access.workspace.workspace_id,
                        parameters_json=json.dumps(
                            parameters,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                        train_run_id=train_record.run.run_id,
                        validation_run_id=validation_record.run.run_id,
                    )
                )
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
                    run_id=candidates[0].validation_run_id,
                    candidates=tuple(candidates),
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
            parameter_grid_json=(
                json.dumps(
                    request.parameter_grid,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                if request.parameter_grid is not None
                else None
            ),
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

        def window_outcome(
            window: BacktestWalkForwardWindowPlan,
            *,
            run_id: UUID,
            status: Literal["queued", "running", "completed", "cancelled", "failed"],
            selected_parameters: str | None,
            in_sample: Decimal | None,
            candidate_count: int,
            result: BacktestResult | None = None,
            error_code: str | None = None,
        ) -> BacktestWalkForwardWindowOutcome:
            metrics = result.metrics if result is not None else None
            return BacktestWalkForwardWindowOutcome(
                schema_version=1,
                workspace_id=workspace_id,
                index=window.index,
                train_start=window.train_start,
                train_end=window.train_end,
                validation_start=window.validation_start,
                validation_end=window.validation_end,
                train_samples=window.train_samples,
                validation_samples=window.validation_samples,
                run_id=run_id,
                status=status,
                total_return=result.summary.total_return if result else None,
                max_drawdown=metrics.max_drawdown if metrics else None,
                sharpe_ratio=metrics.sharpe_ratio if metrics else None,
                trade_count=metrics.trade_count if metrics else None,
                error_code=error_code,
                selected_parameters_json=selected_parameters,
                in_sample_return=in_sample,
                candidate_count=max(1, candidate_count),
            )

        for window in batch.windows:
            selected_parameters = batch.parameters_json
            in_sample: Decimal | None = None
            candidate_count = max(1, len(window.candidates))
            validation_run_id = window.run_id
            if window.candidates:
                states = [
                    (
                        candidate,
                        self._runs.get(workspace_id, candidate.train_run_id).run.status,
                    )
                    for candidate in window.candidates
                ]
                if any(state in {"queued", "running"} for _, state in states):
                    windows.append(
                        window_outcome(
                            window,
                            run_id=window.run_id,
                            status=(
                                "running"
                                if any(state == "running" for _, state in states)
                                else "queued"
                            ),
                            selected_parameters=None,
                            in_sample=None,
                            candidate_count=candidate_count,
                        )
                    )
                    continue
                scored: list[tuple[BacktestWalkForwardCandidate, ParameterCandidate]] = []
                for candidate, state in states:
                    if state != "completed":
                        continue
                    train_result = self.report(access, candidate.train_run_id)
                    if train_result.metrics is None:
                        raise PortError(
                            ErrorCode.CHECKSUM_MISMATCH,
                            "completed in-sample run has no metrics",
                        )
                    scored.append(
                        (
                            candidate,
                            ParameterCandidate(
                                candidate.parameters_json,
                                train_result.summary.total_return,
                                train_result.metrics.max_drawdown,
                            ),
                        )
                    )
                if not scored:
                    windows.append(
                        window_outcome(
                            window,
                            run_id=window.run_id,
                            status="failed",
                            selected_parameters=None,
                            in_sample=None,
                            candidate_count=candidate_count,
                            error_code="candidates_failed",
                        )
                    )
                    continue
                best = choose_parameters([item for _, item in scored])
                selected_candidate = next(
                    candidate
                    for candidate, item in scored
                    if item.parameters_json == best.parameters_json
                )
                selected_parameters = best.parameters_json
                in_sample = best.total_return
                validation_run_id = selected_candidate.validation_run_id
            record = self._runs.get(workspace_id, validation_run_id)
            status = record.run.status
            result = self.report(access, validation_run_id) if status == "completed" else None
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
                window_outcome(
                    window,
                    run_id=validation_run_id,
                    status=status,
                    selected_parameters=selected_parameters,
                    in_sample=in_sample,
                    candidate_count=candidate_count,
                    result=result,
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
            parameter_grid_json=batch.parameter_grid_json,
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
