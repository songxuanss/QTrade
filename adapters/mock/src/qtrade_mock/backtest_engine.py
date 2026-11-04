"""Deterministic, event-driven mock engine; strategy code is never evaluated here."""

import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from qtrade_contracts import (
    ArtifactManifest,
    BacktestEquityPoint,
    BacktestFill,
    BacktestResult,
    BacktestRun,
    Bar,
)
from qtrade_contracts.models import BacktestUniverse, BarGranularity
from qtrade_data_contracts.research import AdjustmentFactor, CorporateAction
from qtrade_domain.backtest_execution import BacktestExecutionModel
from qtrade_domain.backtest_metrics import calculate_backtest_metrics
from qtrade_domain.corporate_actions import CorporateActionLedger
from qtrade_domain.ports import (
    ComputeJob,
    ErrorCode,
    MarketDataPort,
    ObjectStorePort,
    PortError,
    TradingEnginePort,
)
from qtrade_domain.strategy_program import StrategySignal


class ExecutionCancelled(RuntimeError):
    """Cooperative cancellation requested while a backtest is running."""


@dataclass(frozen=True, slots=True)
class MockOrderSignal:
    instrument_id: str
    side: Literal["buy", "sell"]
    quantity: Decimal
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "market"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "gtc"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.instrument_id, str)
            or not self.instrument_id
            or self.side not in {"buy", "sell"}
            or not isinstance(self.quantity, Decimal)
            or not self.quantity.is_finite()
            or self.quantity <= 0
            or self.order_type not in {"market", "limit", "stop", "stop_limit"}
            or self.time_in_force not in {"day", "gtc", "ioc", "fok"}
            or (self.limit_price is not None) != (self.order_type in {"limit", "stop_limit"})
            or (self.stop_price is not None) != (self.order_type in {"stop", "stop_limit"})
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid mock order signal")


@dataclass(frozen=True, slots=True)
class _PendingOrder:
    signal: MockOrderSignal
    submitted_at: datetime
    submitted_index: int
    quantity: Decimal
    filled_quantity: Decimal = Decimal(0)
    filled_notional: Decimal = Decimal(0)
    active_session: date | None = None
    triggered: bool = False


@dataclass(frozen=True, slots=True)
class _Match:
    price: Decimal
    triggered: bool


@dataclass(frozen=True, slots=True)
class _Tradability:
    blocked_side: Literal["buy", "sell", "both"] | None
    reason: Literal["halted", "limit_up", "limit_down", "locked"] | None


class MockStrategyProgram(Protocol):
    """Per-bar signal source used by tests and simple registered models."""

    def on_bar(
        self, bar: Bar, history: tuple[Bar, ...], parameters: dict[str, object]
    ) -> tuple[MockOrderSignal, ...]: ...


class BatchStrategyProgram(Protocol):
    """Evaluate one stored strategy over the complete bar series.

    Implementations may execute source in the isolated Runner. The engine
    consumes only indexed proposals, so each proposal remains tied to the bar
    that produced it and no future bar is exposed to the strategy.
    """

    def signals_for_bars(
        self, bars: tuple[Bar, ...], parameters: dict[str, object]
    ) -> tuple[tuple[StrategySignal, ...], ...]: ...


ProgramResolver = Callable[[UUID, UUID, str, int], MockStrategyProgram | BatchStrategyProgram]


class MockEngineAdapter(TradingEnginePort):
    """Replay immutable bars in timestamp order and convert fills to canonical JSON."""

    _SIGNAL_CHUNK = 5000

    def __init__(
        self,
        market: MarketDataPort,
        objects: ObjectStorePort,
        programs: dict[str, MockStrategyProgram | BatchStrategyProgram],
        resolver: ProgramResolver | None = None,
        signal_chunk_bars: int = 5000,
    ) -> None:
        self._market = market
        self._objects = objects
        self._programs = programs
        self._resolver = resolver
        self._jobs: dict[tuple[UUID, UUID], ComputeJob] = {}
        self._inputs: dict[tuple[UUID, UUID], str] = {}
        if type(signal_chunk_bars) is not int or not 1 <= signal_chunk_bars <= 100_000:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy chunk size")
        MockEngineAdapter._SIGNAL_CHUNK = signal_chunk_bars
        # Set by the Worker to observe cooperative cancellation between chunks.
        self.interrupt: Callable[[], bool] | None = None

    def submit(self, run: BacktestRun, manifest: ArtifactManifest) -> ComputeJob:
        if (
            run.workspace_id != manifest.workspace_id
            or run.run_id != manifest.run_id
            or run.manifest_id != manifest.manifest_id
            or run.dataset_id != manifest.dataset_id
            or run.strategy_version_id != manifest.strategy_version_id
            or manifest.kind != "backtest"
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "run/manifest mismatch")
        try:
            execution_config = json.loads(manifest.execution_config_json or "null")
        except ValueError:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid frozen run configuration") from None
        if not isinstance(execution_config, dict) or any(
            execution_config.get(name) != value
            for name, value in {
                "benchmark_id": run.benchmark_id,
                "cost_model_version": run.cost_model_version,
                "currency": run.currency,
                "end": run.end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "initial_cash": format(run.initial_cash, "f"),
                "start": run.start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            }.items()
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "run differs from frozen manifest inputs")
        raw_actions = execution_config.get("corporate_actions", [])
        if not isinstance(raw_actions, list):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid frozen corporate actions")
        try:
            actions = tuple(
                CorporateAction.model_validate_json(
                    json.dumps(
                        {key: value for key, value in action.items() if key != "content_version"}
                    )
                )
                for action in raw_actions
                if isinstance(action, dict)
            )
            if len(actions) != len(raw_actions):
                raise ValueError
        except ValueError:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid frozen corporate actions") from None
        if any(action.scope_id != run.workspace_id for action in actions):
            raise PortError(ErrorCode.INVALID_INPUT, "corporate action scope mismatch")
        universe = self._universe(execution_config.get("universe"))
        factor_mode = execution_config.get("adjustment_factor_mode", "freeze")
        if factor_mode not in {"freeze", "vendor_adjusted"}:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid adjustment factor mode")
        raw_factors = execution_config.get("adjustment_factors", [])
        if not isinstance(raw_factors, list):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid frozen adjustment factors")
        try:
            factors = tuple(
                AdjustmentFactor.model_validate_json(
                    json.dumps(
                        {key: value for key, value in item.items() if key != "content_version"}
                    )
                )
                for item in raw_factors
                if isinstance(item, dict)
            )
            if len(factors) != len(raw_factors):
                raise ValueError
        except ValueError:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid frozen adjustment factors") from None
        if any(factor.scope_id != run.workspace_id for factor in factors):
            raise PortError(ErrorCode.INVALID_INPUT, "adjustment factor scope mismatch")
        rights_policy = execution_config.get("rights_policy", "subscribe")
        if rights_policy not in {"subscribe", "skip"}:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid frozen rights policy")
        key = (run.workspace_id, run.run_id)
        identity = run.model_dump_json() + manifest.model_dump_json()
        if key in self._inputs and self._inputs[key] != identity:
            raise PortError(ErrorCode.CONFLICT, "run ID reused with different inputs")
        if key in self._jobs:
            return self._jobs[key]
        program = self._programs.get(manifest.code_sha256)
        if program is None and self._resolver is not None:
            program = self._resolver(
                run.workspace_id,
                run.strategy_version_id,
                manifest.code_sha256,
                manifest.random_seed,
            )
        if program is None:
            raise PortError(ErrorCode.UNSUPPORTED, "no strategy program is registered")
        execution_model = self._execution_model(execution_config.get("execution_model"))
        bars, depth = self._load_bars(run, execution_model)
        if factor_mode == "vendor_adjusted":
            bars = self._apply_adjustment_factors(bars, factors, run, execution_model)
        try:
            result = self._replay(
                run,
                manifest,
                bars,
                program,
                execution_model,
                () if factor_mode == "vendor_adjusted" else actions,
                universe,
                rights_policy,
                self.interrupt,
                depth,
            )
        except (PortError, ExecutionCancelled):
            raise
        except Exception:
            raise PortError(ErrorCode.UNAVAILABLE, "mock strategy execution failed") from None
        normalized = BacktestResult.model_validate_json(
            json.dumps(
                {
                    "schema_version": 1,
                    "workspace_id": str(run.workspace_id),
                    **result,
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        payload = json.dumps(
            normalized.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        content_hash = hashlib.sha256(payload).hexdigest()
        ref = self._objects.put(
            run.workspace_id,
            f"backtests/{run.run_id}/result.json",
            payload,
            content_type="application/vnd.qtrade.backtest-result+json",
            expected_sha256=content_hash,
        )
        job = ComputeJob(run.workspace_id, run.run_id, "completed", 100, result=ref)
        self._inputs[key] = identity
        self._jobs[key] = job
        return job

    def status(self, workspace_id: UUID, job_id: UUID) -> ComputeJob:
        try:
            return self._jobs[(workspace_id, job_id)]
        except KeyError:
            raise PortError(ErrorCode.NOT_FOUND, "backtest job not found") from None

    def cancel(self, workspace_id: UUID, job_id: UUID) -> ComputeJob:
        job = self.status(workspace_id, job_id)
        if job.status in {"completed", "failed", "cancelled"}:
            raise PortError(ErrorCode.CONFLICT, "backtest job is already terminal")
        cancelled = ComputeJob(workspace_id, job_id, "cancelled", job.progress)
        self._jobs[(workspace_id, job_id)] = cancelled
        return cancelled

    def _load_bars(
        self, run: BacktestRun, execution: BacktestExecutionModel | None = None
    ) -> tuple[tuple[Bar, ...], dict[tuple[str, datetime], dict[Decimal, Decimal]]]:
        # One ordered dataset read is cheaper than one connection per stream and
        # keeps the sort deterministic. Adapters may expose that bulk read; when
        # they do not (or the series overflows its budget), fall back to
        # concurrent per-instrument pages, which are independent queries.
        from qtrade_domain.ports import BarQuery

        collected: list[Bar]
        bulk = getattr(self._market, "bars_for_dataset", None)
        loaded: tuple[Bar, ...] | None = None
        if callable(bulk):
            loaded = bulk(run.workspace_id, run.dataset_id, run.start, run.end)
        if loaded is not None:
            collected = [bar for bar in loaded if bar.quality in {"valid", "suspect"}]
        else:
            instruments = self._market_instruments(run.workspace_id, run.dataset_id)

            def stream(item: tuple[str, BarGranularity]) -> list[Bar]:
                instrument, granularity = item
                bars: list[Bar] = []
                cursor: str | None = None
                while True:
                    page = self._market.query_bars(
                        BarQuery(
                            run.workspace_id,
                            run.dataset_id,
                            instrument,
                            granularity,
                            run.start,
                            run.end,
                            limit=10000,
                            cursor=cursor,
                        )
                    )
                    # Suspended bars are kept so pending orders can be rejected
                    # explicitly; they are never used for strategy evaluation.
                    bars.extend(bar for bar in page.bars if bar.quality in {"valid", "suspect"})
                    if page.next_cursor is None:
                        break
                    cursor = page.next_cursor
                return bars

            if len(instruments) > 1:
                with ThreadPoolExecutor(max_workers=min(8, len(instruments))) as pool:
                    collected = [bar for bars in pool.map(stream, instruments) for bar in bars]
            else:
                collected = [bar for item in instruments for bar in stream(item)]
        ordered = tuple(sorted(collected, key=lambda bar: (bar.timestamp, bar.instrument_id)))
        depth: dict[tuple[str, datetime], dict[Decimal, Decimal]] = {}
        if ordered and ordered[0].granularity == "tick":
            seconds = execution.tick_aggregation_seconds if execution else 3
            depth = MockEngineAdapter._tick_depth(ordered, seconds)
            ordered = MockEngineAdapter._aggregate_ticks(ordered, seconds)
        if not any(bar.quality == "valid" for bar in ordered):
            raise PortError(
                ErrorCode.INVALID_INPUT, "dataset has no tradable bars in the run range"
            )
        return ordered, depth

    @staticmethod
    def _apply_adjustment_factors(
        bars: tuple[Bar, ...],
        factors: tuple[AdjustmentFactor, ...],
        run: BacktestRun,
        execution: BacktestExecutionModel,
    ) -> tuple[Bar, ...]:
        """Scale execution prices by the frozen vendor factor series.

        The series is expressed relative to the factor observed at the run start;
        cash distributions are disabled by the caller to avoid double counting.
        """
        grouped: dict[str, list[AdjustmentFactor]] = {}
        for factor in factors:
            grouped.setdefault(factor.instrument_id, []).append(factor)
        if not grouped:
            raise PortError(ErrorCode.UNSUPPORTED, "vendor factors are unavailable")
        zone = ZoneInfo(execution.timezone_name)
        start_date = run.start.astimezone(zone).date()

        def factor_at(values: list[AdjustmentFactor], moment: date) -> Decimal:
            ordered = sorted(values, key=lambda item: item.observation_date)
            eligible = [item for item in ordered if item.observation_date <= moment]
            return (eligible[-1] if eligible else ordered[0]).factor

        adjusted: list[Bar] = []
        for bar in bars:
            values = grouped.get(bar.instrument_id)
            if not values:
                adjusted.append(bar)
                continue
            local_date = bar.timestamp.astimezone(zone).date()
            ratio = factor_at(values, local_date) / factor_at(values, start_date)
            if ratio == 1:
                adjusted.append(bar)
                continue
            adjusted.append(
                bar.model_copy(
                    update={
                        "open": bar.open * ratio,
                        "high": bar.high * ratio,
                        "low": bar.low * ratio,
                        "close": bar.close * ratio,
                    }
                )
            )
        return tuple(adjusted)

    @staticmethod
    def _aggregate_ticks(bars: tuple[Bar, ...], seconds: int) -> tuple[Bar, ...]:
        """Aggregate tick prints into fixed execution buckets (open/high/low/close/volume)."""
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid tick aggregation window")
        grouped: dict[tuple[str, int], list[Bar]] = {}
        for bar in bars:
            bucket = int(bar.timestamp.timestamp()) // seconds * seconds
            grouped.setdefault((bar.instrument_id, bucket), []).append(bar)
        aggregated: list[Bar] = []
        for (instrument, bucket), ticks in sorted(grouped.items()):
            ordered = sorted(ticks, key=lambda item: item.timestamp)
            first, last = ordered[0], ordered[-1]
            aggregated.append(
                Bar(
                    schema_version=1,
                    workspace_id=first.workspace_id,
                    instrument_id=instrument,
                    specification_version=first.specification_version,
                    timestamp=datetime.fromtimestamp(bucket, UTC),
                    granularity="tick",
                    open=ordered[0].open,
                    high=max(item.high for item in ordered),
                    low=min(item.low for item in ordered),
                    close=last.close,
                    volume=sum((item.volume for item in ordered), Decimal(0)),
                    value=sum((item.value or Decimal(0) for item in ordered), Decimal(0)),
                    batch_id=first.batch_id,
                    dataset_id=first.dataset_id,
                    quality=(
                        "valid" if all(item.quality == "valid" for item in ordered) else "suspect"
                    ),
                )
            )
        return tuple(aggregated)

    @staticmethod
    def _tick_depth(
        bars: tuple[Bar, ...], seconds: int
    ) -> dict[tuple[str, datetime], dict[Decimal, Decimal]]:
        """Per execution window, the volume printed at each price level.

        This is the observable depth proxy for trade-tick datasets: no order
        book snapshots exist, so the traded volume at each price is the best
        available estimate of liquidity at that level.
        """
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid tick aggregation window")
        depth: dict[tuple[str, datetime], dict[Decimal, Decimal]] = {}
        for bar in bars:
            bucket = int(bar.timestamp.timestamp()) // seconds * seconds
            key = (bar.instrument_id, datetime.fromtimestamp(bucket, UTC))
            levels = depth.setdefault(key, {})
            levels[bar.close] = levels.get(bar.close, Decimal(0)) + bar.volume
        return depth

    def _market_instruments(
        self, workspace_id: UUID, dataset_id: UUID
    ) -> tuple[tuple[str, BarGranularity], ...]:
        """The mock uses its explicit registered bar universe, not metadata guesses."""
        instruments = getattr(self._market, "instruments_for_dataset", None)
        if instruments is None:
            raise PortError(ErrorCode.UNSUPPORTED, "market adapter cannot enumerate dataset bars")
        result = instruments(workspace_id, dataset_id)
        if not result:
            raise PortError(ErrorCode.NOT_FOUND, "dataset bars not found")
        return tuple(sorted(result))

    @staticmethod
    def _match(signal: MockOrderSignal, bar: Bar, triggered: bool) -> "_Match | None":
        stop = signal.stop_price
        limit = signal.limit_price
        if signal.order_type in {"stop", "stop_limit"} and not triggered:
            assert stop is not None
            if signal.side == "buy":
                if bar.high < stop:
                    return None
                reference = max(bar.open, stop)
            else:
                if bar.low > stop:
                    return None
                reference = min(bar.open, stop)
            triggered = True
        else:
            reference = bar.open
        if signal.order_type in {"limit", "stop_limit"} and limit is not None:
            if signal.side == "buy":
                if bar.open <= limit:
                    return _Match(bar.open, triggered)
                if bar.low <= limit:
                    return _Match(limit, triggered)
                return None
            if bar.open >= limit:
                return _Match(bar.open, triggered)
            if bar.high >= limit:
                return _Match(limit, triggered)
            return None
        return _Match(reference, triggered)

    @staticmethod
    def _tradability(
        bar: Bar, previous_close: Decimal | None, execution: BacktestExecutionModel
    ) -> _Tradability:
        if bar.volume <= 0 or bar.quality != "valid":
            return _Tradability("both", "halted")
        if bar.granularity == "1d" and bar.high == bar.low and bar.open == bar.close == bar.high:
            limit = execution.daily_price_limit_fraction
            if limit is None or previous_close is None or previous_close <= 0:
                return _Tradability("both", "locked")
            change = bar.close / previous_close - 1
            tolerance = Decimal("0.001")
            if change >= limit - tolerance:
                return _Tradability("buy", "limit_up")
            if change <= -limit + tolerance:
                return _Tradability("sell", "limit_down")
            return _Tradability("both", "locked")
        return _Tradability(None, None)

    @staticmethod
    def _rejected(order: _PendingOrder, reason: str) -> dict[str, str | None]:
        return {
            "instrument_id": order.signal.instrument_id,
            "side": order.signal.side,
            "quantity": format(order.quantity, "f"),
            "submitted_at": order.submitted_at.isoformat(),
            "status": "rejected",
            "reason": reason,
        }

    @staticmethod
    def _cancelled(order: _PendingOrder, reason: str | None = None) -> dict[str, str | None]:
        average = (
            format(order.filled_notional / order.filled_quantity, "f")
            if order.filled_quantity > 0
            else None
        )
        return {
            "instrument_id": order.signal.instrument_id,
            "side": order.signal.side,
            "quantity": format(order.quantity, "f"),
            "filled_quantity": format(order.filled_quantity, "f"),
            "submitted_at": order.submitted_at.isoformat(),
            "price": average,
            "status": "cancelled",
            "reason": reason,
        }

    @staticmethod
    def _chunked_signals(
        evaluate: Callable[
            [tuple[Bar, ...], dict[str, object]],
            tuple[tuple[StrategySignal, ...], ...],
        ],
        bars: tuple[Bar, ...],
        parameters: dict[str, object],
        interrupt: Callable[[], bool] | None,
    ) -> tuple[tuple[StrategySignal, ...], ...]:
        """Evaluate the strategy in causal chunks and honor cancellation between them."""
        chunk = MockEngineAdapter._SIGNAL_CHUNK
        collected: list[tuple[StrategySignal, ...]] = []
        start = 0
        while start < len(bars):
            if interrupt is not None and interrupt():
                raise ExecutionCancelled("backtest run cancelled")
            end = min(start + chunk, len(bars))
            result = evaluate(bars[:end], parameters)
            if (
                not isinstance(result, tuple)
                or len(result) != end
                or any(not isinstance(signals, tuple) for signals in result)
            ):
                raise PortError(ErrorCode.REJECTED, "invalid batch strategy proposals")
            collected.extend(result[start:end])
            start = end
        if interrupt is not None and interrupt():
            raise ExecutionCancelled("backtest run cancelled")
        return tuple(collected)

    @staticmethod
    def _acceptable_levels(
        levels: dict[Decimal, Decimal], side: str, limit: Decimal | None
    ) -> list[tuple[Decimal, Decimal]]:
        prices = sorted(levels, reverse=side == "sell")
        result: list[tuple[Decimal, Decimal]] = []
        for price in prices:
            if limit is not None:
                if side == "buy" and price > limit:
                    continue
                if side == "sell" and price < limit:
                    continue
            result.append((price, levels[price]))
        return result

    @staticmethod
    def _level_liquidity(
        levels: dict[Decimal, Decimal], side: str, limit: Decimal | None
    ) -> Decimal:
        return sum(
            (volume for _, volume in MockEngineAdapter._acceptable_levels(levels, side, limit)),
            Decimal(0),
        )

    @staticmethod
    def _touch_price(levels: dict[Decimal, Decimal], side: str) -> Decimal | None:
        if not levels:
            return None
        return min(levels) if side == "buy" else max(levels)

    @staticmethod
    def _level_vwap(
        levels: dict[Decimal, Decimal], side: str, quantity: Decimal, limit: Decimal | None
    ) -> Decimal:
        """Volume-weighted price of the prints the order could have traded against.

        Trade ticks carry no order book, so the fill price is the VWAP of the
        eligible price levels in the window (all levels for a market order,
        only levels at or better than the limit otherwise). Level volumes are
        used for capacity and queue priority, not to cherry-pick the cheapest
        print. `quantity` is accepted for signature symmetry and future
        quantity-dependent models.
        """
        del quantity
        eligible = MockEngineAdapter._acceptable_levels(levels, side, limit)
        total = sum((volume for _, volume in eligible), Decimal(0))
        if total <= 0:
            touch = MockEngineAdapter._touch_price(levels, side)
            return touch if touch is not None else Decimal(0)
        return sum((price * volume for price, volume in eligible), Decimal(0)) / total

    @staticmethod
    def _signals(
        program: MockStrategyProgram | BatchStrategyProgram,
        batch_signals: dict[int, tuple[StrategySignal, ...]] | None,
        bar_index: int,
        bar: Bar,
        history: tuple[Bar, ...],
        parameters: dict[str, object],
    ) -> tuple[MockOrderSignal, ...]:
        if bar.quality != "valid":
            return ()
        raw_signals = (
            batch_signals.get(bar_index, ())
            if batch_signals is not None
            else program.on_bar(bar, history, parameters)  # type: ignore[union-attr]
        )
        result: list[MockOrderSignal] = []
        for value in raw_signals:
            if isinstance(value, StrategySignal):
                result.append(
                    MockOrderSignal(
                        value.instrument_id,
                        value.side,
                        value.quantity,
                        value.order_type,
                        value.limit_price,
                        value.stop_price,
                        value.time_in_force,
                    )
                )
            elif isinstance(value, MockOrderSignal):
                # Validate proposals through the canonical signal contract.
                StrategySignal(
                    value.instrument_id,
                    value.side,
                    value.quantity,
                    value.order_type,
                    value.limit_price,
                    value.stop_price,
                    value.time_in_force,
                )
                result.append(value)
            else:
                raise PortError(ErrorCode.REJECTED, "invalid strategy proposal")
        return tuple(result)

    @staticmethod
    def _execution_model(config: object) -> BacktestExecutionModel:
        if not isinstance(config, dict):
            if config is None:
                return BacktestExecutionModel()
            raise PortError(ErrorCode.INVALID_INPUT, "invalid execution model configuration")
        return BacktestExecutionModel.from_config(config)

    @staticmethod
    def _universe(value: object) -> BacktestUniverse | None:
        if value is None:
            return None
        try:
            return BacktestUniverse.model_validate_json(json.dumps(value))
        except ValueError:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid frozen universe") from None

    @staticmethod
    def _membership_windows(
        universe: BacktestUniverse | None,
    ) -> dict[str, tuple[tuple[datetime, datetime], ...]]:
        if universe is None or universe.mode == "dataset":
            return {}
        grouped: dict[str, list[tuple[datetime, datetime]]] = {}
        for item in universe.memberships:
            grouped.setdefault(item.instrument_id, []).append(
                (item.effective_from.astimezone(UTC), item.effective_to.astimezone(UTC))
            )
        return {instrument: tuple(sorted(intervals)) for instrument, intervals in grouped.items()}

    @staticmethod
    def _allows(
        windows: dict[str, tuple[tuple[datetime, datetime], ...]],
        instrument_id: str,
        moment: datetime,
    ) -> bool:
        if not windows:
            return True
        moment = moment.astimezone(UTC)
        return any(start <= moment < end for start, end in windows.get(instrument_id, ()))

    @staticmethod
    def _final_membership_bars(
        bars: tuple[Bar, ...], windows: dict[str, tuple[tuple[datetime, datetime], ...]]
    ) -> set[int]:
        if not windows:
            return set()
        by_instrument: dict[str, list[int]] = {}
        for index, bar in enumerate(bars):
            by_instrument.setdefault(bar.instrument_id, []).append(index)
        final: set[int] = set()
        for instrument, indices in by_instrument.items():
            for position, index in enumerate(indices):
                bar = bars[index]
                if not MockEngineAdapter._allows(windows, instrument, bar.timestamp):
                    continue
                following = indices[position + 1] if position + 1 < len(indices) else None
                if following is None or not MockEngineAdapter._allows(
                    windows, instrument, bars[following].timestamp
                ):
                    final.add(index)
        return final

    @staticmethod
    def _replay(
        run: BacktestRun,
        manifest: ArtifactManifest,
        bars: tuple[Bar, ...],
        program: MockStrategyProgram | BatchStrategyProgram,
        execution: BacktestExecutionModel,
        corporate_actions: tuple[CorporateAction, ...] = (),
        universe: BacktestUniverse | None = None,
        rights_policy: str = "subscribe",
        interrupt: Callable[[], bool] | None = None,
        depth: dict[tuple[str, datetime], dict[Decimal, Decimal]] | None = None,
    ) -> dict[str, object]:
        parameters = json.loads(manifest.parameters_json)
        tradable_indices = tuple(index for index, bar in enumerate(bars) if bar.quality == "valid")
        tradable_bars = tuple(bars[index] for index in tradable_indices)
        batch = getattr(program, "signals_for_bars", None)
        batch_signals: dict[int, tuple[StrategySignal, ...]] | None = None
        if batch is not None:
            chunked = MockEngineAdapter._chunked_signals(
                batch, tradable_bars, parameters, interrupt
            )
            batch_signals = {
                original: signals
                for original, signals in zip(tradable_indices, chunked, strict=True)
            }
        cash = run.initial_cash
        ledger = CorporateActionLedger(corporate_actions, rights_policy)
        membership = MockEngineAdapter._membership_windows(universe)
        final_membership = MockEngineAdapter._final_membership_bars(bars, membership)
        positions: dict[str, Decimal] = {}
        available: dict[str, Decimal] = {}
        history: list[Bar] = []
        pending: list[_PendingOrder] = []
        orders: list[dict[str, str | None]] = []
        fills: list[dict[str, str]] = []
        equity: list[dict[str, object]] = []
        closes: dict[str, Decimal] = {}
        equity_peak = run.initial_cash
        previous_day_closes: dict[str, Decimal] = {}
        market_dates: dict[str, date] = {}
        benchmark_bars = (
            [bar for bar in bars if bar.instrument_id == run.benchmark_id]
            if run.benchmark_id is not None
            else []
        )
        benchmark_index = 0
        benchmark_close: Decimal | None = None
        benchmark_initial = benchmark_bars[0].close if benchmark_bars else None

        for bar_index, bar in enumerate(bars):
            if interrupt is not None and interrupt():
                raise ExecutionCancelled("backtest run cancelled")
            # Signals raised after a close can only fill at a later bar's open.
            local_timestamp = bar.timestamp.astimezone(ZoneInfo(execution.timezone_name))
            market_date = local_timestamp.date()
            if bar.instrument_id in market_dates and market_dates[bar.instrument_id] != market_date:
                previous_day_closes[bar.instrument_id] = closes[bar.instrument_id]
                # Prior-session holdings become sellable at the next session.
                available[bar.instrument_id] = positions.get(bar.instrument_id, Decimal(0))
            market_dates[bar.instrument_id] = market_date
            adjusted = ledger.apply(
                bar.instrument_id,
                market_date,
                positions.get(bar.instrument_id, Decimal(0)),
                available.get(bar.instrument_id, Decimal(0)),
                cash,
                run.currency,
            )
            if adjusted.applied:
                positions[bar.instrument_id] = adjusted.quantity
                available[bar.instrument_id] = adjusted.available
                cash = adjusted.cash
                for subscription in adjusted.subscriptions:
                    fills.append(
                        {
                            "instrument_id": subscription.instrument_id,
                            "side": "buy",
                            "filled_quantity": format(subscription.quantity, "f"),
                            "filled_at": bar.timestamp.isoformat(),
                            "price": format(subscription.price, "f"),
                            "fee": "0",
                        }
                    )
                    orders.append(
                        {
                            "instrument_id": subscription.instrument_id,
                            "side": "buy",
                            "quantity": format(subscription.quantity, "f"),
                            "filled_quantity": format(subscription.quantity, "f"),
                            "submitted_at": bar.timestamp.isoformat(),
                            "filled_at": bar.timestamp.isoformat(),
                            "price": format(subscription.price, "f"),
                            "status": "filled",
                            "reason": "rights_subscription",
                        }
                    )
            member = MockEngineAdapter._allows(membership, bar.instrument_id, bar.timestamp)
            if not member:
                # Bars outside the frozen membership cannot trade or fill.
                pending = [
                    item for item in pending if item.signal.instrument_id != bar.instrument_id
                ]
            local_time = local_timestamp.time()
            still_pending: list[_PendingOrder] = []
            for pending_order in pending:
                signal = pending_order.signal
                submitted_at = pending_order.submitted_at
                if signal.instrument_id != bar.instrument_id:
                    still_pending.append(pending_order)
                    continue
                if (
                    pending_order.active_session is not None
                    and signal.time_in_force == "day"
                    and market_date != pending_order.active_session
                ):
                    orders.append(MockEngineAdapter._cancelled(pending_order))
                    continue
                if bar_index - pending_order.submitted_index < execution.latency_bars:
                    still_pending.append(pending_order)
                    continue
                if pending_order.active_session is None:
                    pending_order = replace(pending_order, active_session=market_date)
                if bar.granularity != "1d" and not execution.in_session(local_time):
                    still_pending.append(pending_order)
                    continue
                tradability = MockEngineAdapter._tradability(
                    bar, previous_day_closes.get(bar.instrument_id), execution
                )
                if tradability.reason is not None and tradability.blocked_side in {
                    "both",
                    signal.side,
                }:
                    if tradability.reason == "halted" and signal.time_in_force == "gtc":
                        # A suspended instrument pauses GTC orders instead of rejecting them.
                        still_pending.append(pending_order)
                        continue
                    orders.append(
                        MockEngineAdapter._cancelled(pending_order, tradability.reason)
                        if pending_order.filled_quantity > 0
                        else MockEngineAdapter._rejected(pending_order, tradability.reason)
                    )
                    continue
                matched = MockEngineAdapter._match(signal, bar, pending_order.triggered)
                if matched is None:
                    if signal.time_in_force in {"ioc", "fok"}:
                        orders.append(MockEngineAdapter._cancelled(pending_order))
                    else:
                        still_pending.append(pending_order)
                    continue
                pending_order = replace(pending_order, triggered=matched.triggered)
                price = execution.fill_price(signal.side, matched.price)
                if signal.limit_price is not None:
                    price = (
                        min(price, signal.limit_price)
                        if signal.side == "buy"
                        else max(price, signal.limit_price)
                    )
                if not execution.within_daily_limit(
                    price, previous_day_closes.get(bar.instrument_id)
                ):
                    if signal.time_in_force in {"ioc", "fok"}:
                        orders.append(MockEngineAdapter._cancelled(pending_order))
                    else:
                        still_pending.append(pending_order)
                    continue
                quantity = execution.round_quantity(
                    pending_order.quantity - pending_order.filled_quantity
                )
                if signal.side == "sell":
                    quantity = min(quantity, available.get(bar.instrument_id, Decimal(0)))
                if quantity <= 0:
                    if signal.time_in_force in {"ioc", "fok"}:
                        orders.append(MockEngineAdapter._cancelled(pending_order))
                    else:
                        still_pending.append(pending_order)
                    continue
                levels = depth.get((bar.instrument_id, bar.timestamp)) if depth else None
                volume_cap = execution.volume_capacity(bar.volume)
                if levels:
                    volume_cap = min(
                        volume_cap,
                        MockEngineAdapter._level_liquidity(levels, signal.side, signal.limit_price),
                    )
                    touch = MockEngineAdapter._touch_price(levels, signal.side)
                    if touch is not None and (
                        signal.limit_price is not None
                        or execution.at_daily_limit(
                            price, previous_day_closes.get(bar.instrument_id)
                        )
                    ):
                        volume_cap = min(
                            volume_cap,
                            execution.limit_queue_capacity(levels.get(touch, Decimal(0))),
                        )
                elif execution.at_daily_limit(price, previous_day_closes.get(bar.instrument_id)):
                    volume_cap = min(volume_cap, execution.limit_queue_capacity(bar.volume))
                affordable = cash / price if signal.side == "buy" else quantity
                filled = (
                    min(quantity, affordable, volume_cap)
                    if signal.side == "buy"
                    else min(quantity, volume_cap)
                )
                # Affordability and volume are continuous values; fills must
                # still respect the instrument lot before cash is debited.
                filled = execution.round_quantity(filled)
                lot = Decimal(execution.lot_size)

                if filled > 0 and levels:
                    # Tick runs walk the observed price levels instead of using
                    # the bar-level square-root impact approximation.
                    price = execution.round_to_tick(
                        signal.side,
                        MockEngineAdapter._level_vwap(
                            levels, signal.side, filled, signal.limit_price
                        ),
                    )
                elif filled > 0 and bar.volume > 0 and execution.impact_bps > 0:
                    price = execution.impact_price(signal.side, price, filled / bar.volume)
                    if signal.limit_price is not None:
                        price = (
                            min(price, signal.limit_price)
                            if signal.side == "buy"
                            else max(price, signal.limit_price)
                        )
                while (
                    signal.side == "buy"
                    and filled > 0
                    and filled * price + execution.fee(filled * price, "buy") > cash
                ):
                    filled -= lot
                    if filled > 0 and levels:
                        price = execution.round_to_tick(
                            signal.side,
                            MockEngineAdapter._level_vwap(
                                levels, signal.side, filled, signal.limit_price
                            ),
                        )
                if signal.time_in_force == "fok" and filled < quantity:
                    orders.append(MockEngineAdapter._cancelled(pending_order))
                    continue
                if filled <= 0:
                    if volume_cap <= 0:
                        if signal.time_in_force in {"ioc", "fok"}:
                            orders.append(MockEngineAdapter._cancelled(pending_order))
                        else:
                            still_pending.append(pending_order)
                        continue
                    if pending_order.filled_quantity > 0:
                        if signal.time_in_force in {"ioc", "fok"}:
                            orders.append(MockEngineAdapter._cancelled(pending_order))
                        else:
                            still_pending.append(pending_order)
                        continue
                    orders.append(
                        {
                            "instrument_id": bar.instrument_id,
                            "side": signal.side,
                            "quantity": format(pending_order.quantity, "f"),
                            "submitted_at": submitted_at.isoformat(),
                            "status": "rejected",
                        }
                    )
                    continue
                signed = filled if signal.side == "buy" else -filled
                notional = filled * price
                fee = execution.fee(notional, signal.side)
                cash -= signed * price + fee
                positions[bar.instrument_id] = positions.get(bar.instrument_id, Decimal(0)) + signed
                if signal.side == "buy":
                    if execution.settlement_days == 0:
                        available[bar.instrument_id] = positions[bar.instrument_id]
                else:
                    available[bar.instrument_id] = max(
                        available.get(bar.instrument_id, Decimal(0)) - filled,
                        Decimal(0),
                    )
                fills.append(
                    {
                        "instrument_id": signal.instrument_id,
                        "side": signal.side,
                        "filled_quantity": format(filled, "f"),
                        "filled_at": bar.timestamp.isoformat(),
                        "price": format(price, "f"),
                        "fee": format(fee, "f"),
                    }
                )
                filled_total = pending_order.filled_quantity + filled
                filled_notional = pending_order.filled_notional + notional
                completed = _PendingOrder(
                    signal,
                    submitted_at,
                    pending_order.submitted_index,
                    pending_order.quantity,
                    filled_total,
                    filled_notional,
                    pending_order.active_session,
                    pending_order.triggered,
                )
                if filled_total >= pending_order.quantity:
                    orders.append(
                        {
                            "instrument_id": signal.instrument_id,
                            "side": signal.side,
                            "quantity": format(pending_order.quantity, "f"),
                            "filled_quantity": format(filled_total, "f"),
                            "submitted_at": submitted_at.isoformat(),
                            "filled_at": bar.timestamp.isoformat(),
                            "price": format(filled_notional / filled_total, "f"),
                            "status": "filled",
                        }
                    )
                elif signal.time_in_force in {"ioc", "fok"}:
                    orders.append(MockEngineAdapter._cancelled(completed))
                else:
                    still_pending.append(completed)
            pending = still_pending
            closes[bar.instrument_id] = bar.close
            if bar_index in final_membership and positions.get(bar.instrument_id, Decimal(0)) > 0:
                # Leaving the universe settles the whole position at the last
                # observable close; T+1 does not apply to a membership exit.
                quantity = positions[bar.instrument_id]
                notional = quantity * bar.close
                fee = execution.fee(notional, "sell")
                cash += notional - fee
                positions[bar.instrument_id] = Decimal(0)
                available[bar.instrument_id] = Decimal(0)
                pending = [
                    item for item in pending if item.signal.instrument_id != bar.instrument_id
                ]
                fills.append(
                    {
                        "instrument_id": bar.instrument_id,
                        "side": "sell",
                        "filled_quantity": format(quantity, "f"),
                        "filled_at": bar.timestamp.isoformat(),
                        "price": format(bar.close, "f"),
                        "fee": format(fee, "f"),
                    }
                )
                orders.append(
                    {
                        "instrument_id": bar.instrument_id,
                        "side": "sell",
                        "quantity": format(quantity, "f"),
                        "filled_quantity": format(quantity, "f"),
                        "submitted_at": bar.timestamp.isoformat(),
                        "filled_at": bar.timestamp.isoformat(),
                        "price": format(bar.close, "f"),
                        "status": "filled",
                        "reason": "membership_end",
                    }
                )
            marked = cash + sum(
                (
                    quantity * closes.get(instrument, Decimal(0))
                    for instrument, quantity in positions.items()
                ),
                Decimal(0),
            )
            while (
                benchmark_index < len(benchmark_bars)
                and benchmark_bars[benchmark_index].timestamp <= bar.timestamp
            ):
                benchmark_close = benchmark_bars[benchmark_index].close
                benchmark_index += 1
            equity_peak = max(equity_peak, marked)
            if bar_index + 1 == len(bars) or bars[bar_index + 1].timestamp != bar.timestamp:
                equity.append(
                    {
                        "timestamp": bar.timestamp.isoformat(),
                        "cash": format(cash, "f"),
                        "equity": format(marked, "f"),
                        "drawdown": (
                            format((equity_peak - marked) / equity_peak, "f")
                            if equity_peak > 0
                            else "0"
                        ),
                        "benchmark_equity": (
                            format(run.initial_cash * benchmark_close / benchmark_initial, "f")
                            if benchmark_close is not None
                            and benchmark_initial is not None
                            and benchmark_initial > 0
                            else None
                        ),
                        "positions": [
                            {
                                "instrument_id": instrument,
                                "quantity": format(quantity, "f"),
                                "market_value": format(quantity * closes[instrument], "f"),
                            }
                            for instrument, quantity in sorted(positions.items())
                            if quantity != 0
                        ],
                    }
                )
            signals = (
                ()
                if not member
                else MockEngineAdapter._signals(
                    program,
                    batch_signals,
                    bar_index,
                    bar,
                    tuple(history),
                    parameters,
                )
            )
            for signal in signals:
                requested = execution.round_quantity(signal.quantity)
                if requested <= 0:
                    orders.append(
                        {
                            "instrument_id": signal.instrument_id,
                            "side": signal.side,
                            "quantity": format(signal.quantity, "f"),
                            "submitted_at": bar.timestamp.isoformat(),
                            "status": "rejected",
                        }
                    )
                else:
                    pending.append(
                        _PendingOrder(
                            MockOrderSignal(
                                signal.instrument_id,
                                signal.side,
                                requested,
                                signal.order_type,
                                signal.limit_price,
                                signal.stop_price,
                                signal.time_in_force,
                            ),
                            bar.timestamp,
                            bar_index,
                            requested,
                        )
                    )
            if member and bar.quality == "valid":
                history.append(bar)

        for pending_order in pending:
            signal = pending_order.signal
            submitted_at = pending_order.submitted_at
            status = (
                "partially_filled_end_of_data"
                if pending_order.filled_quantity > 0
                else "unfilled_end_of_data"
            )
            orders.append(
                {
                    "instrument_id": signal.instrument_id,
                    "side": signal.side,
                    "quantity": format(pending_order.quantity, "f"),
                    "filled_quantity": format(pending_order.filled_quantity, "f"),
                    "submitted_at": submitted_at.isoformat(),
                    "price": (
                        format(
                            pending_order.filled_notional / pending_order.filled_quantity,
                            "f",
                        )
                        if pending_order.filled_quantity > 0
                        else None
                    ),
                    "status": status,
                }
            )
        last = Decimal(str(equity[-1]["equity"]))
        peak = max(Decimal(str(point["equity"])) for point in equity)
        ending_drawdown = Decimal(0) if peak == 0 else (peak - last) / peak
        metric_points = tuple(
            BacktestEquityPoint.model_validate_json(json.dumps(point)) for point in equity
        )
        benchmark_return = None
        if benchmark_bars and benchmark_bars[0].close > 0:
            benchmark_return = benchmark_bars[-1].close / benchmark_bars[0].close - 1
        metrics = calculate_backtest_metrics(
            metric_points,
            periods_per_year=execution.periods_per_year,
            benchmark_return=benchmark_return,
            fills=tuple(BacktestFill.model_validate_json(json.dumps(fill)) for fill in fills),
            initial_cash=run.initial_cash,
        )
        return {
            "schema_version": 1,
            "determinism": {"engine": "qtrade.sandbox-event.v1", "seed": manifest.random_seed},
            "dataset_id": str(run.dataset_id),
            "strategy_version_id": str(run.strategy_version_id),
            "currency": run.currency,
            "equity_curve": equity,
            "orders": orders,
            "fills": fills,
            "summary": {
                "initial_cash": str(run.initial_cash),
                "final_equity": format(last, "f"),
                "total_return": format((last - run.initial_cash) / run.initial_cash, "f"),
                "ending_drawdown": str(ending_drawdown),
                "sample_count": len(equity),
            },
            "metrics": metrics.model_dump(mode="json"),
        }
