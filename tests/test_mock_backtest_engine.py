"""The mock engine replays deterministic signals and normalizes immutable results."""

from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from qtrade_contracts import ArtifactManifest, BacktestRun, Bar
from qtrade_domain.backtest_execution import BacktestExecutionModel
from qtrade_domain.strategy_program import StrategySignal
from qtrade_mock.backtest_engine import MockEngineAdapter, MockOrderSignal
from qtrade_mock.ports import MockMarketData, MockObjectStore

FIXTURES = Path(__file__).parent / "fixtures/contracts"


class BuyOnce:
    def on_bar(self, bar, history, parameters):
        if not history:
            return (MockOrderSignal(bar.instrument_id, "buy", Decimal("5")),)
        return ()


class BuyTwoHundredOnce:
    def on_bar(self, bar, history, parameters):
        if not history:
            return (MockOrderSignal(bar.instrument_id, "buy", Decimal("200")),)
        return ()


class NoTrades:
    def on_bar(self, bar, history, parameters):
        return ()


def test_mock_engine_replay_is_event_ordered_and_repeatable():
    bar_template = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run_template = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest_template = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    workspace_id, dataset_id = uuid4(), uuid4()
    base_time = bar_template.timestamp
    bars = tuple(
        bar_template.model_copy(
            update={
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
                "timestamp": base_time + timedelta(minutes=index),
                "open": Decimal(opening),
                "close": Decimal(closing),
                "low": min(Decimal(opening), Decimal(closing)),
                "high": max(Decimal(opening), Decimal(closing)),
            }
        )
        for index, (opening, closing) in enumerate(((10, 10), (12, 13), (13, 14)))
    )
    market = MockMarketData()
    market.write_bars(workspace_id, dataset_id, tuple(reversed(bars)))
    objects = MockObjectStore()
    adapter = MockEngineAdapter(market, objects, {manifest_template.code_sha256: BuyOnce()})

    def submit_once():
        run_id, manifest_id = uuid4(), uuid4()
        run = run_template.model_copy(
            update={
                "workspace_id": workspace_id,
                "run_id": run_id,
                "manifest_id": manifest_id,
                "dataset_id": dataset_id,
                "start": run_template.start,
                "end": run_template.end,
                "initial_cash": run_template.initial_cash,
            }
        )
        manifest = manifest_template.model_copy(
            update={
                "workspace_id": workspace_id,
                "run_id": run_id,
                "manifest_id": manifest_id,
                "dataset_id": dataset_id,
            }
        )
        return adapter.submit(run, manifest)

    first = submit_once()
    second = submit_once()
    assert first.status == second.status == "completed"
    assert first.result is not None and second.result is not None
    assert first.result.content_sha256 == second.result.content_sha256
    result = objects.get(
        workspace_id, first.result.key, expected_sha256=first.result.content_sha256
    )
    import json

    normalized = json.loads(result)
    assert normalized["fills"][0]["filled_at"] == "2026-10-01T00:01:00Z"
    assert normalized["fills"][0]["price"] == "12"
    assert normalized["summary"]["final_equity"] == "10010"


def test_mock_engine_execution_model_applies_a_share_lot_and_rejects_sub_lot_order():
    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    result = MockEngineAdapter._replay(
        run,
        manifest,
        (bar,),
        BuyOnce(),
        BacktestExecutionModel(
            lot_size=100,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("100"),
            max_volume_fraction=Decimal("0.5"),
        ),
    )
    assert result["fills"] == []
    assert result["orders"] == [
        {
            "instrument_id": "MOCK:ABC",
            "quantity": "5",
            "side": "buy",
            "submitted_at": "2026-10-01T00:00:00+00:00",
            "status": "rejected",
        }
    ]
    assert result["equity_curve"][-1]["equity"] == str(run.initial_cash)


def test_mock_engine_partial_fills_include_costs_and_session_rules():
    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    bars = tuple(
        bar.model_copy(
            update={
                "timestamp": bar.timestamp + timedelta(minutes=index),
                "open": Decimal("10"),
                "close": Decimal("10"),
            }
        )
        for index in range(3)
    )
    result = MockEngineAdapter._replay(
        run,
        manifest,
        bars,
        BuyTwoHundredOnce(),
        BacktestExecutionModel(
            lot_size=100,
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("100"),
            max_volume_fraction=Decimal("1"),
        ),
    )
    assert [fill["filled_quantity"] for fill in result["fills"]] == ["100", "100"]
    assert [fill["price"] for fill in result["fills"]] == ["10.10", "10.10"]
    assert [fill["fee"] for fill in result["fills"]] == ["1.01", "1.01"]
    assert result["equity_curve"][-1]["equity"] == "9977.98"
    assert len(result["orders"]) == 1
    assert result["orders"][0]["status"] == "filled"
    assert result["orders"][0]["quantity"] == "200"
    assert result["orders"][0]["filled_quantity"] == "200"


def test_mock_engine_does_not_create_sub_lot_partial_fills():
    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    result = MockEngineAdapter._replay(
        run,
        manifest,
        (bar,),
        BuyTwoHundredOnce(),
        BacktestExecutionModel(lot_size=100, max_volume_fraction=Decimal("0.5")),
    )
    assert result["fills"] == []
    assert result["orders"][0]["status"] == "unfilled_end_of_data"
    assert result["orders"][0]["filled_quantity"] == "0"


def test_mock_engine_reports_aligned_benchmark_return_and_flat_positions():
    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    run = run.model_copy(update={"benchmark_id": "MOCK:BM"})
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    times = (bar.timestamp, bar.timestamp + timedelta(days=1))
    bars = tuple(
        bar.model_copy(
            update={
                "instrument_id": instrument,
                "timestamp": timestamp,
                "open": price,
                "close": price,
                "high": price,
                "low": price,
            }
        )
        for instrument, prices in (
            ("MOCK:ABC", (Decimal("10"), Decimal("10"))),
            ("MOCK:BM", (Decimal("100"), Decimal("110"))),
        )
        for timestamp, price in zip(times, prices, strict=True)
    )
    result = MockEngineAdapter._replay(
        run,
        manifest,
        tuple(sorted(bars, key=lambda item: (item.timestamp, item.instrument_id))),
        NoTrades(),
        BacktestExecutionModel(),
    )
    assert result["metrics"]["benchmark_return"] == "0.1"
    assert [point["benchmark_equity"] for point in result["equity_curve"]] == [
        "10000",
        "11000",
    ]
    assert result["equity_curve"][-1]["positions"] == []


class BuyAllIn:
    def on_bar(self, bar, history, parameters):
        if not history:
            return (MockOrderSignal(bar.instrument_id, "buy", Decimal("1000")),)
        return ()


def test_all_in_buy_is_lot_rounded_and_serializes_plain_decimals():
    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    workspace_id, dataset_id = uuid4(), uuid4()
    run = run.model_copy(
        update={
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "initial_cash": Decimal("10000"),
        }
    )
    manifest = manifest.model_copy(update={"workspace_id": workspace_id, "dataset_id": dataset_id})
    rich_bar = bar.model_copy(
        update={
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "open": Decimal("10.1"),
            "close": Decimal("10.1"),
            "low": Decimal("10.1"),
            "high": Decimal("10.1"),
        }
    )
    bars = (
        rich_bar,
        rich_bar.model_copy(update={"timestamp": rich_bar.timestamp + timedelta(minutes=1)}),
    )
    market = MockMarketData()
    market.write_bars(workspace_id, dataset_id, bars)
    adapter = MockEngineAdapter(
        market,
        MockObjectStore(),
        {manifest.code_sha256: BuyAllIn()},
    )
    job = adapter.submit(run, manifest)
    assert job.status == "completed"
    result = adapter._jobs[(workspace_id, run.run_id)]
    assert result.result is not None


def test_daily_bars_fill_without_an_intraday_session_window():
    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    daily = bar.model_copy(
        update={
            "granularity": "1d",
            "timestamp": bar.timestamp.replace(hour=0, minute=0),
            "open": Decimal("10"),
            "close": Decimal("10"),
        }
    )
    bars = (daily, daily.model_copy(update={"timestamp": daily.timestamp + timedelta(days=1)}))
    result = MockEngineAdapter._replay(
        run,
        manifest,
        bars,
        BuyOnce(),
        BacktestExecutionModel(
            lot_size=1,
            latency_bars=1,
            session_open=time(9, 30),
            session_close=time(15, 0),
            timezone_name="Asia/Shanghai",
        ),
    )
    assert len(result["fills"]) == 1


class BuySecondBarBatch:
    def signals_for_bars(self, bars, parameters):
        return (
            (),
            (StrategySignal(bars[1].instrument_id, "buy", Decimal("100")),),
        )


def test_batch_program_proposals_are_bound_to_their_bar():
    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    bars = (
        bar.model_copy(update={"timestamp": bar.timestamp}),
        bar.model_copy(update={"timestamp": bar.timestamp + timedelta(minutes=1)}),
    )
    result = MockEngineAdapter._replay(
        run, manifest, bars, BuySecondBarBatch(), BacktestExecutionModel(lot_size=100)
    )
    assert result["fills"] == []
    assert result["orders"][0]["submitted_at"] == bars[1].timestamp.isoformat()


class BuyAndSellFirst:
    def on_bar(self, bar, history, parameters):
        if history:
            return ()
        return (
            MockOrderSignal(bar.instrument_id, "buy", Decimal("100")),
            MockOrderSignal(bar.instrument_id, "sell", Decimal("100")),
        )


def test_t_plus_one_blocks_same_session_sell_until_next_day():
    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    first = bar.model_copy(update={"open": Decimal("10"), "close": Decimal("10")})
    second = first.model_copy(update={"timestamp": first.timestamp + timedelta(minutes=1)})
    next_day = first.model_copy(update={"timestamp": first.timestamp + timedelta(days=1)})
    result = MockEngineAdapter._replay(
        run,
        manifest,
        (first, second, next_day),
        BuyAndSellFirst(),
        BacktestExecutionModel(lot_size=100, settlement_days=1),
    )
    assert len(result["fills"]) == 2
    assert result["fills"][0]["side"] == "buy"
    assert result["fills"][1]["side"] == "sell"
    assert result["fills"][1]["filled_at"] == next_day.timestamp.isoformat()
    assert len(result["orders"]) == 2
