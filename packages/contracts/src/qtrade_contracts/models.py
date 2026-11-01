"""Versioned wire contracts. No persistence, provider, or engine private types."""

import json
import re
from datetime import date, time
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
    model_validator,
)

Text = Annotated[str, Field(min_length=1, max_length=256)]
JSONText = Annotated[str, Field(min_length=1, max_length=1_000_000)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageDigest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
BarGranularity = Literal["tick", "1m", "5m", "15m", "30m", "1h", "1d"]
Granularity = Literal["tick", "1m", "5m", "15m", "30m", "1h", "1d"]
OrderType = Literal["market", "limit", "stop", "stop_limit"]
Side = Literal["buy", "sell"]
OrderStatus = Literal[
    "created",
    "risk_approved",
    "submitted",
    "partially_filled",
    "cancel_pending",
    "filled",
    "cancelled",
    "rejected",
    "timeout",
    "failed",
]


def decimal_value(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise ValueError("decimal quantities require a decimal string, integer, or Decimal")
    if isinstance(value, str) and not re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", value):
        raise ValueError("decimal strings must use plain base-10 notation")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("invalid decimal") from error
    if not result.is_finite():
        raise ValueError("decimal must be finite")
    return result


DecimalValue = Annotated[
    Decimal,
    BeforeValidator(decimal_value),
    PlainSerializer(lambda v: format(v, "f")),
    WithJsonSchema({"type": "string", "pattern": r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$"}),
]
PositiveDecimal = Annotated[
    DecimalValue,
    Field(gt=0),
    WithJsonSchema(
        {"type": "string", "pattern": r"^(?:0\.0*[1-9][0-9]*|[1-9][0-9]*(?:\.[0-9]+)?)$"}
    ),
]
NonNegativeDecimal = Annotated[
    DecimalValue,
    Field(ge=0),
    WithJsonSchema({"type": "string", "pattern": r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"}),
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1]
    workspace_id: UUID

    @field_validator("schema_version", mode="before")
    @classmethod
    def explicit_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported schema version")
        return value


class Instrument(Contract):
    instrument_id: Text
    specification_version: Annotated[int, Field(ge=1)]
    name: Text
    market: Text
    exchange: Text
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3,10}$")]
    tick_size: PositiveDecimal
    lot_size: PositiveDecimal
    contract_multiplier: PositiveDecimal
    price_precision: Annotated[int, Field(ge=0, le=18)]
    quantity_precision: Annotated[int, Field(ge=0, le=18)]
    timezone: Text
    calendar_id: Text
    enabled: bool

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("unknown IANA timezone") from error
        return value


LocalClock = Annotated[
    time,
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9](?:\.[0-9]{1,6})?)?$",
        }
    ),
]


class CalendarSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    opens: LocalClock
    closes: LocalClock
    close_day_offset: Annotated[int, Field(ge=0, le=1)] = 0
    open_fold: Annotated[int, Field(ge=0, le=1)] | None = None
    close_fold: Annotated[int, Field(ge=0, le=1)] | None = None

    @field_validator("opens", "closes")
    @classmethod
    def local_clock(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("session clocks must be local without a timezone offset")
        return value


class CalendarOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    trading_date: date
    sessions: tuple[CalendarSession, ...]


class CalendarSchedule(Contract):
    calendar_id: Text
    calendar_version: Annotated[int, Field(ge=1)]
    timezone: Text
    effective_from: date
    effective_to: date
    weekdays: tuple[Annotated[int, Field(ge=0, le=6)], ...]
    sessions: tuple[CalendarSession, ...]
    overrides: tuple[CalendarOverride, ...] = ()

    @field_validator("timezone")
    @classmethod
    def timezone_name(cls, value: str) -> str:
        return Instrument.valid_timezone(value)

    @model_validator(mode="after")
    def coherent_calendar(self) -> Self:
        if self.effective_from > self.effective_to or len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("invalid calendar range or weekdays")
        dates = [item.trading_date for item in self.overrides]
        if len(set(dates)) != len(dates) or any(
            not self.effective_from <= day <= self.effective_to for day in dates
        ):
            raise ValueError("invalid calendar overrides")
        return self


class Bar(Contract):
    instrument_id: Text
    specification_version: Annotated[int, Field(ge=1)]
    timestamp: AwareDatetime
    granularity: BarGranularity
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: NonNegativeDecimal
    value: NonNegativeDecimal | None = None
    batch_id: UUID
    dataset_id: UUID
    quality: Literal["valid", "suspect", "quarantined"]

    @model_validator(mode="after")
    def coherent_ohlc(self) -> Self:
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC prices fall outside low/high")
        if self.low > self.high:
            raise ValueError("low exceeds high")
        return self


class DataBatch(Contract):
    batch_id: UUID
    source: Text
    authorization_id: Text
    ingestion_task_id: UUID
    instrument_id: Text
    granularity: Granularity
    collected_at: AwareDatetime
    object_key: Text
    content_sha256: Hash
    record_count: Annotated[int, Field(ge=0)]


class MarketDataset(Contract):
    dataset_id: UUID
    version: Annotated[int, Field(ge=1)]
    created_at: AwareDatetime
    batch_ids: Annotated[tuple[UUID, ...], Field(min_length=1)]
    instrument_ids: Annotated[tuple[Text, ...], Field(min_length=1)]
    granularity: Granularity
    start: AwareDatetime
    end: AwareDatetime
    transformation_version: Text
    quality_report_key: Text
    content_sha256: Hash

    @model_validator(mode="after")
    def coherent_range(self) -> Self:
        if self.start >= self.end:
            raise ValueError("dataset range must be nonempty [start, end)")
        if len(set(self.batch_ids)) != len(self.batch_ids):
            raise ValueError("duplicate source batch")
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("duplicate instrument")
        return self


def canonical_object(value: str) -> str:
    def no_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = item
        return result

    parsed = json.loads(
        value,
        object_pairs_hook=no_duplicates,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
    )
    if not isinstance(parsed, dict):
        raise ValueError("expected a JSON object")
    return json.dumps(
        parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


class StrategyVersion(Contract):
    strategy_id: UUID
    strategy_version_id: UUID
    version: Annotated[int, Field(ge=1)]
    created_by: UUID
    created_at: AwareDatetime
    code_object_key: Text
    code_sha256: Hash
    parameter_schema_json: str
    dependencies_sha256: Hash
    dependencies_object_key: Text | None = None

    @field_validator("parameter_schema_json")
    @classmethod
    def canonical_schema(cls, value: str) -> str:
        return canonical_object(value)


class ArtifactManifest(Contract):
    manifest_id: UUID
    run_id: UUID
    kind: Literal["backtest", "research", "paper", "live"]
    created_by: UUID
    created_at: AwareDatetime
    strategy_version_id: UUID
    code_sha256: Hash
    dataset_id: UUID
    dataset_sha256: Hash
    parameters_json: str
    parameters_sha256: Hash
    engine_name: Text
    engine_version: Text
    adapter_version: Text
    environment_image_digest: ImageDigest
    random_seed: Annotated[int, Field(ge=0)]
    nondeterministic_sources: tuple[Text, ...] = ()
    execution_config_json: str | None = None
    execution_config_sha256: Hash | None = None

    @field_validator("parameters_json")
    @classmethod
    def canonical_parameters(cls, value: str) -> str:
        return canonical_object(value)

    @model_validator(mode="after")
    def matching_parameter_hash(self) -> Self:
        from hashlib import sha256

        if sha256(self.parameters_json.encode()).hexdigest() != self.parameters_sha256:
            raise ValueError("parameter hash does not match canonical JSON")
        if (self.execution_config_json is None) != (self.execution_config_sha256 is None):
            raise ValueError("execution configuration and hash must be provided together")
        if self.execution_config_json is not None:
            canonical = canonical_object(self.execution_config_json)
            if canonical != self.execution_config_json:
                raise ValueError("execution configuration must be canonical JSON")
            if sha256(canonical.encode()).hexdigest() != self.execution_config_sha256:
                raise ValueError("execution configuration hash does not match")
        return self


class BacktestRun(Contract):
    run_id: UUID
    strategy_version_id: UUID
    dataset_id: UUID
    manifest_id: UUID
    created_by: UUID
    created_at: AwareDatetime
    start: AwareDatetime
    end: AwareDatetime
    initial_cash: PositiveDecimal
    currency: Text
    benchmark_id: Text | None
    cost_model_version: Text
    status: Literal["queued", "running", "cancelled", "failed", "completed"]
    progress: Annotated[int, Field(ge=0, le=100)]
    result_object_key: Text | None = None
    error_code: Text | None = None

    @model_validator(mode="after")
    def coherent_state(self) -> Self:
        if self.start >= self.end:
            raise ValueError("backtest range must be nonempty [start, end)")
        if self.status == "completed" and (self.progress != 100 or not self.result_object_key):
            raise ValueError("completed run requires full progress and a result")
        if self.status == "failed" and not self.error_code:
            raise ValueError("failed run requires an error code")
        return self


class BacktestPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument_id: Text
    quantity: DecimalValue
    market_value: DecimalValue


class BacktestEquityPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    timestamp: AwareDatetime
    cash: DecimalValue
    equity: DecimalValue
    positions: tuple[BacktestPosition, ...] = ()
    drawdown: NonNegativeDecimal = Decimal(0)
    benchmark_equity: DecimalValue | None = None


class BacktestOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument_id: Text
    side: Side
    quantity: PositiveDecimal
    filled_quantity: NonNegativeDecimal | None = None
    submitted_at: AwareDatetime
    filled_at: AwareDatetime | None = None
    price: PositiveDecimal | None = None
    status: Literal[
        "filled",
        "rejected",
        "cancelled",
        "partially_filled_end_of_data",
        "unfilled_end_of_data",
    ]
    reason: (
        Literal[
            "signal",
            "membership_end",
            "rights_subscription",
            "halted",
            "limit_up",
            "limit_down",
            "locked",
        ]
        | None
    ) = None


class BacktestFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument_id: Text
    side: Side
    filled_quantity: PositiveDecimal
    filled_at: AwareDatetime
    price: PositiveDecimal
    fee: NonNegativeDecimal = Decimal(0)


class BacktestSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    initial_cash: PositiveDecimal
    final_equity: DecimalValue
    total_return: DecimalValue
    ending_drawdown: NonNegativeDecimal
    sample_count: Annotated[int, Field(ge=1)]


class BacktestDeterminism(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    engine: Text
    seed: Annotated[int, Field(ge=0)]


class BacktestMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    max_drawdown: NonNegativeDecimal
    annualized_return: DecimalValue | None = None
    annualized_volatility: NonNegativeDecimal | None = None
    sharpe_ratio: DecimalValue | None = None
    sortino_ratio: DecimalValue | None = None
    benchmark_return: DecimalValue | None = None
    calmar_ratio: DecimalValue | None = None
    win_rate: NonNegativeDecimal | None = None
    profit_factor: NonNegativeDecimal | None = None
    turnover: NonNegativeDecimal = Decimal(0)
    average_exposure: NonNegativeDecimal = Decimal(0)
    max_exposure: NonNegativeDecimal = Decimal(0)
    trade_count: Annotated[int, Field(ge=0)] = 0
    winning_trades: Annotated[int, Field(ge=0)] = 0
    losing_trades: Annotated[int, Field(ge=0)] = 0


class BacktestResult(Contract):
    dataset_id: UUID
    strategy_version_id: UUID
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3,10}$")]
    determinism: BacktestDeterminism
    equity_curve: Annotated[tuple[BacktestEquityPoint, ...], Field(min_length=1)]
    orders: tuple[BacktestOrder, ...]
    fills: tuple[BacktestFill, ...]
    summary: BacktestSummary
    metrics: BacktestMetrics | None = None


class BacktestComparisonRun(Contract):
    run_id: UUID
    dataset_id: UUID
    strategy_version_id: UUID
    start: AwareDatetime
    end: AwareDatetime
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3,10}$")]
    benchmark_id: Text | None = None
    execution_config_json: str
    result: BacktestResult

    @model_validator(mode="after")
    def coherent_report(self) -> Self:
        if (
            self.start >= self.end
            or self.result.workspace_id != self.workspace_id
            or self.result.dataset_id != self.dataset_id
            or self.result.strategy_version_id != self.strategy_version_id
        ):
            raise ValueError("comparison report does not match its run inputs")
        return self


class BacktestComparison(Contract):
    runs: Annotated[tuple[BacktestComparisonRun, ...], Field(min_length=2, max_length=20)]
    comparable: bool
    warnings: tuple[Text, ...]
    input_differences: tuple[Text, ...]

    @model_validator(mode="after")
    def same_workspace(self) -> Self:
        if any(run.workspace_id != self.workspace_id for run in self.runs):
            raise ValueError("comparison runs must belong to one workspace")
        if len({run.run_id for run in self.runs}) != len(self.runs):
            raise ValueError("comparison runs must be unique")
        return self


class BacktestWalkForwardWindow(Contract):
    train_start: AwareDatetime
    train_end: AwareDatetime
    validation_start: AwareDatetime
    validation_end: AwareDatetime
    train_samples: Annotated[int, Field(ge=1)]
    validation_samples: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def chronological_ranges(self) -> Self:
        if (
            self.train_start >= self.train_end
            or self.train_end != self.validation_start
            or self.validation_start >= self.validation_end
        ):
            raise ValueError("walk-forward windows must be ordered and nonempty")
        return self


class BacktestWalkForwardPlan(Contract):
    dataset_id: UUID
    instrument_id: Text
    start: AwareDatetime
    end: AwareDatetime
    train_window_seconds: Annotated[int, Field(gt=0)]
    validation_window_seconds: Annotated[int, Field(gt=0)]
    step_seconds: Annotated[int, Field(gt=0)]
    windows: Annotated[tuple[BacktestWalkForwardWindow, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def disjoint_validation_windows(self) -> Self:
        if self.start >= self.end or any(
            left.validation_end > right.validation_start
            for left, right in zip(self.windows, self.windows[1:], strict=False)
        ):
            raise ValueError("walk-forward validation windows must not overlap")
        return self


class BacktestWalkForwardCandidate(Contract):
    """One frozen parameter set evaluated in-sample and out-of-sample."""

    parameters_json: JSONText
    train_run_id: UUID
    validation_run_id: UUID


class BacktestWalkForwardWindowPlan(Contract):
    index: Annotated[int, Field(ge=1)]
    train_start: AwareDatetime
    train_end: AwareDatetime
    validation_start: AwareDatetime
    validation_end: AwareDatetime
    train_samples: Annotated[int, Field(ge=1)]
    validation_samples: Annotated[int, Field(ge=1)]
    run_id: UUID
    candidates: tuple[BacktestWalkForwardCandidate, ...] = ()

    @model_validator(mode="after")
    def chronological(self) -> Self:
        if (
            self.train_start >= self.train_end
            or self.train_end != self.validation_start
            or self.validation_start >= self.validation_end
        ):
            raise ValueError("walk-forward window must be ordered and nonempty")
        return self


class BacktestWalkForwardBatch(Contract):
    walk_forward_id: UUID
    created_at: AwareDatetime
    created_by: UUID
    strategy_id: UUID
    strategy_version: Annotated[int, Field(ge=1)]
    strategy_version_id: UUID
    dataset_id: UUID
    instrument_id: Text
    start: AwareDatetime
    end: AwareDatetime
    initial_cash: PositiveDecimal
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    benchmark_id: Text | None = None
    cost_model_version: Text
    parameters_json: JSONText
    execution_config_json: JSONText
    random_seed: Annotated[int, Field(ge=0)]
    train_window_seconds: Annotated[int, Field(gt=0)]
    validation_window_seconds: Annotated[int, Field(gt=0)]
    step_seconds: Annotated[int, Field(gt=0)]
    parameter_grid_json: JSONText | None = None
    windows: Annotated[tuple[BacktestWalkForwardWindowPlan, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def ordered_windows(self) -> Self:
        if self.start >= self.end or any(
            left.validation_end > right.validation_start
            for left, right in zip(self.windows, self.windows[1:], strict=False)
        ):
            raise ValueError("walk-forward windows must be ordered and disjoint")
        return self


class BacktestWalkForwardWindowOutcome(Contract):
    index: Annotated[int, Field(ge=1)]
    train_start: AwareDatetime
    train_end: AwareDatetime
    validation_start: AwareDatetime
    validation_end: AwareDatetime
    train_samples: Annotated[int, Field(ge=1)]
    validation_samples: Annotated[int, Field(ge=1)]
    run_id: UUID
    status: Literal["queued", "running", "completed", "cancelled", "failed"]
    total_return: DecimalValue | None = None
    max_drawdown: NonNegativeDecimal | None = None
    sharpe_ratio: DecimalValue | None = None
    trade_count: Annotated[int, Field(ge=0)] | None = None
    error_code: Text | None = None
    selected_parameters_json: JSONText | None = None
    in_sample_return: DecimalValue | None = None
    candidate_count: Annotated[int, Field(ge=1)] = 1

    @model_validator(mode="after")
    def chronologized_metrics(self) -> Self:
        if (
            self.train_start >= self.train_end
            or self.train_end != self.validation_start
            or self.validation_start >= self.validation_end
        ):
            raise ValueError("walk-forward outcome window must be ordered")
        completed = self.status == "completed"
        measured = (
            self.total_return is not None
            and self.max_drawdown is not None
            and self.trade_count is not None
        )
        if completed != measured:
            raise ValueError("walk-forward outcome metrics must match its status")
        return self


class BacktestWalkForwardAggregate(Contract):
    completed_windows: Annotated[int, Field(ge=0)]
    failed_windows: Annotated[int, Field(ge=0)]
    mean_validation_return: DecimalValue | None = None
    median_validation_return: DecimalValue | None = None
    mean_max_drawdown: NonNegativeDecimal | None = None
    worst_max_drawdown: NonNegativeDecimal | None = None
    positive_window_ratio: NonNegativeDecimal | None = None
    compounded_validation_return: DecimalValue | None = None
    total_trade_count: Annotated[int, Field(ge=0)] = 0


class BacktestWalkForwardRun(Contract):
    walk_forward_id: UUID
    created_at: AwareDatetime
    strategy_id: UUID
    strategy_version: Annotated[int, Field(ge=1)]
    dataset_id: UUID
    instrument_id: Text
    start: AwareDatetime
    end: AwareDatetime
    initial_cash: PositiveDecimal
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    benchmark_id: Text | None = None
    cost_model_version: Text
    parameters_json: JSONText
    execution_config_json: JSONText
    random_seed: Annotated[int, Field(ge=0)]
    train_window_seconds: Annotated[int, Field(gt=0)]
    validation_window_seconds: Annotated[int, Field(gt=0)]
    step_seconds: Annotated[int, Field(gt=0)]
    parameter_grid_json: JSONText | None = None
    status: Literal["queued", "running", "completed", "partial", "failed"]
    windows: Annotated[tuple[BacktestWalkForwardWindowOutcome, ...], Field(min_length=1)]
    aggregate: BacktestWalkForwardAggregate | None = None

    @model_validator(mode="after")
    def consistent_rollup(self) -> Self:
        completed = sum(item.status == "completed" for item in self.windows)
        failed = sum(item.status in {"failed", "cancelled"} for item in self.windows)
        pending = sum(item.status in {"queued", "running"} for item in self.windows)
        expected = (
            "running"
            if any(item.status == "running" for item in self.windows)
            else "queued"
            if pending
            else "completed"
            if completed == len(self.windows)
            else "partial"
            if completed
            else "failed"
        )
        if self.status != expected:
            raise ValueError("walk-forward status must follow its window statuses")
        if completed and (
            self.aggregate is None
            or self.aggregate.completed_windows != completed
            or self.aggregate.failed_windows != failed
        ):
            raise ValueError("walk-forward aggregate must match completed windows")
        if not completed and self.aggregate is not None:
            raise ValueError("walk-forward aggregate requires a completed window")
        return self


class UniverseMembership(Contract):
    """Workspace-defined effective window for a research universe member."""

    membership_id: UUID
    instrument_id: Text
    effective_from: AwareDatetime
    effective_to: AwareDatetime
    source: Literal["workspace.definition", "supplier.membership"]
    evidence: Text
    content_version: Hash
    created_by: UUID
    created_at: AwareDatetime

    @model_validator(mode="after")
    def ordered_interval(self) -> Self:
        if self.effective_from >= self.effective_to:
            raise ValueError("membership interval must be nonempty")
        return self


class BacktestUniverseMembership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument_id: Text
    effective_from: AwareDatetime
    effective_to: AwareDatetime
    source: Text
    evidence: Text
    content_version: Hash

    @model_validator(mode="after")
    def ordered_interval(self) -> Self:
        if self.effective_from >= self.effective_to:
            raise ValueError("frozen membership interval must be nonempty")
        return self


class BacktestUniverse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    mode: Literal["dataset", "membership"]
    memberships: tuple[BacktestUniverseMembership, ...] = ()
    instruments: Annotated[int, Field(ge=0)] = 0
    full_coverage: Annotated[int, Field(ge=0)] = 0
    partial_coverage: Annotated[int, Field(ge=0)] = 0
    unknown: Annotated[int, Field(ge=0)] = 0
    disclosure: Text

    @model_validator(mode="after")
    def consistent_coverage(self) -> Self:
        if (
            self.full_coverage + self.partial_coverage + self.unknown != self.instruments
            or (self.mode == "dataset" and self.memberships)
            or (self.mode == "membership" and self.unknown)
        ):
            raise ValueError("universe coverage must match its memberships")
        return self


class OrderIntent(Contract):
    intent_id: UUID
    account_id: UUID
    runtime_id: UUID
    strategy_version_id: UUID
    created_by: UUID
    created_at: AwareDatetime
    instrument_id: Text
    side: Side
    order_type: OrderType
    quantity: PositiveDecimal
    limit_price: PositiveDecimal | None = None
    stop_price: PositiveDecimal | None = None
    time_in_force: Literal["day", "gtc", "ioc", "fok"]
    trace_id: UUID

    @model_validator(mode="after")
    def coherent_prices(self) -> Self:
        if (self.limit_price is not None) != (self.order_type in {"limit", "stop_limit"}):
            raise ValueError("limit price must match the order type")
        if (self.stop_price is not None) != (self.order_type in {"stop", "stop_limit"}):
            raise ValueError("stop price must match the order type")
        return self


class RiskReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    rule_id: Text
    rule_version: Text
    code: Text
    summary: Text
    current_value: DecimalValue | None = None
    limit_value: DecimalValue | None = None


class RiskDecision(Contract):
    decision_id: UUID
    intent_id: UUID
    decided_at: AwareDatetime
    outcome: Literal["allow", "deny", "require_approval"]
    rule_set_version: Text
    input_sha256: Hash
    reasons: tuple[RiskReason, ...]
    trace_id: UUID

    @model_validator(mode="after")
    def explain_rejection(self) -> Self:
        if self.outcome != "allow" and not self.reasons:
            raise ValueError("denial or approval requirement must be explained")
        return self


class OrderCommand(Contract):
    command_id: UUID
    idempotency_key: UUID
    intent: OrderIntent
    decision: RiskDecision
    authorized_at: AwareDatetime

    @model_validator(mode="after")
    def authorized_intent(self) -> Self:
        if self.decision.outcome != "allow":
            raise ValueError("only an allowed decision can authorize a command")
        if self.intent.intent_id != self.decision.intent_id:
            raise ValueError("risk decision belongs to another intent")
        if (
            self.workspace_id != self.intent.workspace_id
            or self.workspace_id != self.decision.workspace_id
        ):
            raise ValueError("cross-workspace order command")
        if self.intent.trace_id != self.decision.trace_id:
            raise ValueError("risk decision correlation mismatch")
        return self


class Order(Contract):
    order_id: UUID
    account_id: UUID
    runtime_id: UUID
    intent_id: UUID
    command_id: UUID
    idempotency_key: UUID
    instrument_id: Text
    side: Side
    status: OrderStatus
    quantity: PositiveDecimal
    filled_quantity: NonNegativeDecimal = Decimal(0)
    average_fill_price: PositiveDecimal | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: Annotated[int, Field(ge=1)]
    terminal_reason: Text | None = None

    @model_validator(mode="after")
    def coherent_fills(self) -> Self:
        if self.filled_quantity > self.quantity:
            raise ValueError("filled quantity cannot exceed order quantity")
        if (self.filled_quantity > 0) != (self.average_fill_price is not None):
            raise ValueError("filled orders require an average fill price")
        if self.status == "filled" and self.filled_quantity != self.quantity:
            raise ValueError("filled status requires the full order quantity")
        if self.status == "partially_filled" and not (0 < self.filled_quantity < self.quantity):
            raise ValueError("partially filled status requires a partial quantity")
        if self.updated_at < self.created_at:
            raise ValueError("order update cannot precede creation")
        return self


class OrderEvent(Contract):
    event_id: UUID
    order_id: UUID
    revision: Annotated[int, Field(ge=1)]
    from_status: OrderStatus | None
    to_status: OrderStatus
    occurred_at: AwareDatetime
    source: Literal["risk", "broker", "user", "recovery", "system"]
    filled_quantity: NonNegativeDecimal
    reason: Text | None = None

    @model_validator(mode="after")
    def coherent_revision(self) -> Self:
        if (self.from_status is None) != (self.revision == 1):
            raise ValueError("only order creation has no previous status")
        return self


CONTRACTS: tuple[type[Contract], ...] = (
    CalendarSchedule,
    Instrument,
    Bar,
    DataBatch,
    MarketDataset,
    StrategyVersion,
    BacktestRun,
    BacktestResult,
    BacktestComparisonRun,
    BacktestWalkForwardPlan,
    BacktestWalkForwardWindow,
    BacktestWalkForwardBatch,
    BacktestWalkForwardRun,
    BacktestWalkForwardWindowOutcome,
    BacktestWalkForwardWindowPlan,
    UniverseMembership,
    BacktestComparison,
    OrderIntent,
    RiskDecision,
    OrderCommand,
    Order,
    OrderEvent,
    ArtifactManifest,
)
