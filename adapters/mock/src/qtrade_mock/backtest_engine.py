"""Deterministic, event-driven mock engine; strategy code is never evaluated here."""

import hashlib
import json
from collections.abc import Callable
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
from qtrade_data_contracts.research import CorporateAction
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

    def __init__(
        self,
        market: MarketDataPort,
        objects: ObjectStorePort,
        programs: dict[str, MockStrategyProgram | BatchStrategyProgram],
        resolver: ProgramResolver | None = None,
    ) -> None:
        self._market = market
        self._objects = objects
        self._programs = programs
        self._resolver = resolver
        self._jobs: dict[tuple[UUID, UUID], ComputeJob] = {}
        self._inputs: dict[tuple[UUID, UUID], str] = {}

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
        bars = self._load_bars(run)
        try:
            result = self._replay(
                run,
                manifest,
                bars,
                program,
                self._execution_model(execution_config.get("execution_model")),
                actions,
                universe,
                rights_policy,
            )
        except PortError:
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

    def _load_bars(self, run: BacktestRun) -> tuple[Bar, ...]:
        # Query one deterministic page stream per instrument. The dataset and bars
        # are immutable, so a stable sort creates a stable event order across adapters.
        from qtrade_domain.ports import BarQuery

        instruments = self._market_instruments(run.workspace_id, run.dataset_id)
        collected: list[Bar] = []
        for instrument, granularity in instruments:
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
                collected.extend(bar for bar in page.bars if bar.quality in {"valid", "suspect"})
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
        ordered = tuple(sorted(collected, key=lambda bar: (bar.timestamp, bar.instrument_id)))
        if not any(bar.quality == "valid" for bar in ordered):
            raise PortError(
                ErrorCode.INVALID_INPUT, "dataset has no tradable bars in the run range"
            )
        return ordered

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
    ) -> dict[str, object]:
        parameters = json.loads(manifest.parameters_json)
        tradable_indices = tuple(index for index, bar in enumerate(bars) if bar.quality == "valid")
        tradable_bars = tuple(bars[index] for index in tradable_indices)
        batch = getattr(program, "signals_for_bars", None)
        batch_result = batch(tradable_bars, parameters) if batch is not None else None
        if batch_result is not None and (
            not isinstance(batch_result, tuple)
            or len(batch_result) != len(tradable_bars)
            or any(not isinstance(signals, tuple) for signals in batch_result)
        ):
            raise PortError(ErrorCode.REJECTED, "invalid batch strategy proposals")
        batch_signals = (
            dict(zip(tradable_indices, batch_result, strict=True))
            if batch_result is not None
            else None
        )
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
                volume_cap = execution.volume_capacity(bar.volume)
                if execution.at_daily_limit(price, previous_day_closes.get(bar.instrument_id)):
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
                if filled > 0 and bar.volume > 0 and execution.impact_bps > 0:
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
