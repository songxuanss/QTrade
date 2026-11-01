"""The mock engine replays deterministic signals and normalizes immutable results."""

from datetime import UTC, date, datetime, time, timedelta
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


class BuyHundredOnce:
    def on_bar(self, bar, history, parameters):
        if not history:
            return (MockOrderSignal(bar.instrument_id, "buy", Decimal("100")),)
        return ()


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
    instrument = "stock:CN:SH:600519"
    first = bar.model_copy(
        update={
            "instrument_id": instrument,
            "open": Decimal("10"),
            "close": Decimal("10"),
        }
    )
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


def test_corporate_action_cash_and_bonus_shares_are_applied_on_ex_date():
    from qtrade_data_contracts.research import CorporateAction

    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    instrument = "stock:CN:SH:600519"
    day = bar.model_copy(
        update={
            "instrument_id": instrument,
            "open": Decimal("10"),
            "close": Decimal("10"),
        }
    )
    bars = (
        day,
        day.model_copy(update={"timestamp": day.timestamp + timedelta(days=1)}),
        day.model_copy(update={"timestamp": day.timestamp + timedelta(days=2)}),
    )
    action = CorporateAction.model_validate(
        {
            "schema_version": 2,
            "scope_id": run.workspace_id,
            "source": "fixture",
            "instrument_id": instrument,
            "collected_at": datetime(2026, 1, 1, tzinfo=UTC),
            "mapping_version": "fixture.v1",
            "source_event_id": "dividend-1",
            "action": "distribution",
            "announcement_date": date(2026, 9, 20),
            "record_date": date(2026, 10, 1),
            "ex_date": date(2026, 10, 3),
            "status": "implemented",
            "cash": Decimal("5"),
            "cash_currency": "USD",
            "cash_tax_basis": "before_tax",
            "basis_shares": Decimal("10"),
            "bonus_shares": Decimal("3"),
            "terms_evidence": "fixture",
        }
    )
    result = MockEngineAdapter._replay(
        run,
        manifest,
        bars,
        BuyHundredOnce(),
        BacktestExecutionModel(lot_size=100),
        (action,),
    )
    assert result["fills"][0]["filled_quantity"] == "100"
    assert result["equity_curve"][-1]["positions"][0]["quantity"] == "130"
    assert Decimal(result["equity_curve"][-1]["cash"]) == Decimal("10000") - Decimal(
        "1000"
    ) + Decimal("50")


def membership_config(start, end, instrument="MOCK:ABC"):
    from qtrade_contracts import BacktestUniverse, BacktestUniverseMembership

    return BacktestUniverse(
        mode="membership",
        memberships=(
            BacktestUniverseMembership(
                instrument_id=instrument,
                effective_from=start,
                effective_to=end,
                source="workspace.definition",
                evidence="fixture interval",
                content_version="a" * 64,
            ),
        ),
        instruments=1,
        full_coverage=0,
        partial_coverage=1,
        unknown=0,
        disclosure="fixture disclosure",
    )


def test_membership_end_liquidates_position_at_last_available_close():
    from datetime import UTC, datetime

    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    day = bar.model_copy(update={"open": Decimal("10"), "close": Decimal("10")})
    second = day.model_copy(
        update={
            "timestamp": day.timestamp + timedelta(days=1),
            "close": Decimal("12"),
        }
    )
    third = day.model_copy(update={"timestamp": day.timestamp + timedelta(days=2)})
    universe = membership_config(
        datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, 12, tzinfo=UTC)
    )
    result = MockEngineAdapter._replay(
        run,
        manifest,
        (day, second, third),
        BuyHundredOnce(),
        BacktestExecutionModel(lot_size=100),
        universe=universe,
    )

    exit_orders = [order for order in result["orders"] if order.get("reason") == "membership_end"]
    assert len(exit_orders) == 1
    assert exit_orders[0]["filled_at"] == second.timestamp.isoformat()
    assert exit_orders[0]["price"] == "12"
    assert exit_orders[0]["filled_quantity"] == "100"
    assert result["equity_curve"][-1]["positions"] == []
    assert Decimal(result["equity_curve"][-1]["cash"]) == Decimal("10000") + Decimal("200")


def test_rights_subscription_emits_fill_and_reconciles_cash():
    from datetime import UTC, date, datetime

    from qtrade_data_contracts.research import CorporateAction

    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    instrument = "stock:CN:SH:600519"
    first = bar.model_copy(
        update={
            "instrument_id": instrument,
            "open": Decimal("10"),
            "close": Decimal("10"),
        }
    )
    second = first.model_copy(update={"timestamp": first.timestamp + timedelta(days=1)})
    third = second.model_copy(update={"timestamp": second.timestamp + timedelta(days=1)})
    action = CorporateAction.model_validate(
        {
            "schema_version": 2,
            "scope_id": run.workspace_id,
            "source": "fixture",
            "instrument_id": instrument,
            "collected_at": datetime(2026, 1, 1, tzinfo=UTC),
            "mapping_version": "fixture.v1",
            "source_event_id": "rights-1",
            "action": "rights",
            "announcement_date": date(2026, 9, 1),
            "ex_date": date(2026, 10, 3),
            "status": "implemented",
            "basis_shares": Decimal("10"),
            "rights_shares": Decimal("3"),
            "rights_price": Decimal("5"),
            "terms_evidence": "fixture",
        }
    )
    result = MockEngineAdapter._replay(
        run,
        manifest,
        (first, second, third),
        BuyHundredOnce(),
        BacktestExecutionModel(lot_size=100),
        (action,),
        rights_policy="subscribe",
    )

    subscriptions = [
        order for order in result["orders"] if order.get("reason") == "rights_subscription"
    ]
    assert len(subscriptions) == 1
    assert subscriptions[0]["filled_quantity"] == "30"
    assert subscriptions[0]["price"] == "5"
    assert result["equity_curve"][-1]["positions"][0]["quantity"] == "130"
    assert Decimal(result["equity_curve"][-1]["cash"]) == Decimal("8850")


def test_rights_skip_policy_keeps_cash_and_quantity():
    from datetime import UTC, date, datetime

    from qtrade_data_contracts.research import CorporateAction

    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    instrument = "stock:CN:SH:600519"
    first = bar.model_copy(
        update={
            "instrument_id": instrument,
            "open": Decimal("10"),
            "close": Decimal("10"),
        }
    )
    second = first.model_copy(update={"timestamp": first.timestamp + timedelta(days=1)})
    third = second.model_copy(update={"timestamp": second.timestamp + timedelta(days=1)})
    action = CorporateAction.model_validate(
        {
            "schema_version": 2,
            "scope_id": run.workspace_id,
            "source": "fixture",
            "instrument_id": instrument,
            "collected_at": datetime(2026, 1, 1, tzinfo=UTC),
            "mapping_version": "fixture.v1",
            "source_event_id": "rights-2",
            "action": "rights",
            "announcement_date": date(2026, 9, 1),
            "ex_date": date(2026, 10, 3),
            "status": "implemented",
            "basis_shares": Decimal("10"),
            "rights_shares": Decimal("3"),
            "rights_price": Decimal("5"),
            "terms_evidence": "fixture",
        }
    )
    result = MockEngineAdapter._replay(
        run,
        manifest,
        (first, second, third),
        BuyHundredOnce(),
        BacktestExecutionModel(lot_size=100),
        (action,),
        rights_policy="skip",
    )

    assert not [order for order in result["orders"] if order.get("reason") == "rights_subscription"]
    assert result["equity_curve"][-1]["positions"][0]["quantity"] == "100"
    assert Decimal(result["equity_curve"][-1]["cash"]) == Decimal("9000")


def _bar(day: int, *, open_price="10", high="11", low="9", close="10", volume="500"):
    template = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    return template.model_copy(
        update={
            "granularity": "1d",
            "timestamp": template.timestamp + timedelta(days=day),
            "open": Decimal(open_price),
            "high": Decimal(high),
            "low": Decimal(low),
            "close": Decimal(close),
            "volume": Decimal(volume),
        }
    )


class OnceSignal:
    def __init__(self, signal):
        self.signal = signal

    def on_bar(self, bar, history, parameters):
        return () if history else (self.signal,)


def replay_orders(signal, bars, execution=None):
    template = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    prepared = tuple(
        bar.model_copy(update={"instrument_id": template.instrument_id}) for bar in bars
    )
    return MockEngineAdapter._replay(
        run,
        manifest,
        prepared,
        OnceSignal(signal),
        execution or BacktestExecutionModel(lot_size=100),
    )


def test_limit_order_fills_at_open_or_limit_and_day_orders_expire():
    marketable = MockOrderSignal("MOCK:ABC", "buy", Decimal("100"), "limit", Decimal("10.5"))
    result = replay_orders(
        marketable,
        (_bar(0), _bar(1, open_price="10", high="11", low="9")),
    )
    assert result["fills"][0]["price"] == "10"

    inside_low = MockOrderSignal("MOCK:ABC", "buy", Decimal("100"), "limit", Decimal("9.5"))
    result = replay_orders(
        inside_low,
        (_bar(0), _bar(1, open_price="10", high="11", low="9")),
    )
    assert result["fills"][0]["price"] == "9.5"

    unfilled = MockOrderSignal(
        "MOCK:ABC", "buy", Decimal("100"), "limit", Decimal("5"), time_in_force="day"
    )
    result = replay_orders(
        unfilled,
        (
            _bar(0),
            _bar(1, open_price="10", high="11", low="9"),
            _bar(2, open_price="10", high="11", low="9"),
        ),
    )
    assert result["fills"] == []
    assert result["orders"][0]["status"] == "cancelled"


def test_stop_order_triggers_on_high_and_gtc_limit_waits():
    stop = MockOrderSignal("MOCK:ABC", "buy", Decimal("100"), "stop", stop_price=Decimal("10.5"))
    result = replay_orders(stop, (_bar(0), _bar(1, open_price="10", high="11", low="9")))
    assert result["fills"][0]["price"] == "10.5"

    waiting = MockOrderSignal(
        "MOCK:ABC",
        "buy",
        Decimal("100"),
        "limit",
        Decimal("8"),
        time_in_force="gtc",
    )
    result = replay_orders(
        waiting,
        (
            _bar(0),
            _bar(1, open_price="10", high="11", low="9"),
            _bar(2, open_price="9", high="9.5", low="8.5"),
            _bar(3, open_price="8.4", high="8.6", low="7.9"),
        ),
    )
    assert result["fills"][0]["price"] == "8"
    assert result["orders"][0]["status"] == "filled"


def test_ioc_fills_available_quantity_then_cancels_remainder():
    ioc = MockOrderSignal("MOCK:ABC", "buy", Decimal("100"), time_in_force="ioc")
    result = replay_orders(
        ioc,
        (_bar(0), _bar(1, volume="50")),
        BacktestExecutionModel(lot_size=1, max_volume_fraction=Decimal("1")),
    )
    assert result["fills"][0]["filled_quantity"] == "50"
    assert result["orders"][0]["status"] == "cancelled"
    assert result["orders"][0]["filled_quantity"] == "50"


def test_fok_rejects_when_full_quantity_is_not_fillable():
    fok = MockOrderSignal("MOCK:ABC", "buy", Decimal("100"), time_in_force="fok")
    result = replay_orders(
        fok,
        (_bar(0), _bar(1, volume="50")),
        BacktestExecutionModel(lot_size=1, max_volume_fraction=Decimal("1")),
    )
    assert result["fills"] == []
    assert result["orders"][0]["status"] == "cancelled"
    assert result["orders"][0]["filled_quantity"] == "0"


def test_stop_limit_requires_both_trigger_and_limit():
    stop_limit = MockOrderSignal(
        "MOCK:ABC",
        "buy",
        Decimal("100"),
        "stop_limit",
        limit_price=Decimal("10.2"),
        stop_price=Decimal("10.5"),
    )
    triggered = replay_orders(
        stop_limit, (_bar(0), _bar(1, open_price="10.4", high="11", low="10.1"))
    )
    assert triggered["fills"][0]["price"] == "10.2"

    not_triggered = replay_orders(
        stop_limit, (_bar(0), _bar(1, open_price="9", high="10", low="8"))
    )
    assert not_triggered["fills"] == []


class ScriptedSignals:
    def __init__(self, script):
        self.script = script

    def on_bar(self, bar, history, parameters):
        index = len(history)
        return tuple(self.script[index]) if index < len(self.script) else ()


def replay_script(script, bars, execution=None):
    template = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    manifest = ArtifactManifest.model_validate_json(
        (FIXTURES / "ArtifactManifest.json").read_text()
    )
    prepared = tuple(
        bar.model_copy(update={"instrument_id": template.instrument_id, "granularity": "1d"})
        for bar in bars
    )
    return MockEngineAdapter._replay(
        run,
        manifest,
        prepared,
        ScriptedSignals(script),
        execution
        or BacktestExecutionModel(lot_size=100, daily_price_limit_fraction=Decimal("0.1")),
    )


def test_halted_bar_rejects_day_orders_and_pauses_gtc_orders():
    day = MockOrderSignal("MOCK:ABC", "buy", Decimal("100"), time_in_force="day")
    result = replay_script(
        ((day,),),
        (_bar(0), _bar(1, volume="0")),
    )
    assert result["fills"] == []
    assert result["orders"][0]["status"] == "rejected"
    assert result["orders"][0]["reason"] == "halted"

    gtc = MockOrderSignal("MOCK:ABC", "buy", Decimal("100"), time_in_force="gtc")
    resumed = replay_script(
        ((gtc,),),
        (_bar(0), _bar(1, volume="0"), _bar(2, volume="500")),
    )
    assert len(resumed["fills"]) == 1
    assert resumed["fills"][0]["filled_at"] == resumed["equity_curve"][2]["timestamp"]


def test_limit_up_blocks_buy_orders_and_allows_sells():
    buy = MockOrderSignal("MOCK:ABC", "buy", Decimal("100"))
    locked_up = replay_script(
        ((buy,),),
        (_bar(0, close="10"), _bar(1, open_price="11", high="11", low="11", close="11")),
    )
    assert locked_up["fills"] == []
    assert locked_up["orders"][0]["status"] == "rejected"
    assert locked_up["orders"][0]["reason"] == "limit_up"

    script = (
        (MockOrderSignal("MOCK:ABC", "buy", Decimal("100")),),
        (MockOrderSignal("MOCK:ABC", "sell", Decimal("100")),),
    )
    sold = replay_script(
        script,
        (
            _bar(0, close="10"),
            _bar(1, close="10"),
            _bar(2, open_price="11", high="11", low="11", close="11"),
        ),
    )
    assert [fill["side"] for fill in sold["fills"]] == ["buy", "sell"]
    assert sold["fills"][1]["price"] == "11"


def test_limit_down_blocks_sell_orders_and_allows_buys():
    script = (
        (MockOrderSignal("MOCK:ABC", "buy", Decimal("100")),),
        (MockOrderSignal("MOCK:ABC", "sell", Decimal("100")),),
    )
    result = replay_script(
        script,
        (
            _bar(0, close="10"),
            _bar(1, close="10"),
            _bar(2, open_price="9", high="9", low="9", close="9"),
        ),
    )
    assert [fill["side"] for fill in result["fills"]] == ["buy"]
    rejected = [order for order in result["orders"] if order["status"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "limit_down"

    buy_at_limit_down = replay_script(
        ((MockOrderSignal("MOCK:ABC", "buy", Decimal("100")),),),
        (_bar(0, close="10"), _bar(1, open_price="9", high="9", low="9", close="9")),
    )
    assert buy_at_limit_down["fills"][0]["price"] == "9"


def test_locked_bar_without_limit_configuration_is_rejected_as_locked():
    locked = replay_script(
        ((MockOrderSignal("MOCK:ABC", "buy", Decimal("100")),),),
        (_bar(0, close="10"), _bar(1, open_price="10", high="10", low="10", close="10")),
        BacktestExecutionModel(lot_size=100),
    )
    assert locked["fills"] == []
    assert locked["orders"][0]["reason"] == "locked"


def test_market_impact_worsens_fill_price_with_participation():
    buy = MockOrderSignal("MOCK:ABC", "buy", Decimal("100"))
    result = replay_orders(
        buy,
        (_bar(0), _bar(1, open_price="10", high="10.2", low="9.9", volume="1000")),
        BacktestExecutionModel(lot_size=100, tick_size=Decimal("0.01"), impact_bps=Decimal("100")),
    )
    assert result["fills"][0]["price"] == "10.04"
    notional = Decimal("100") * Decimal("10.04")
    assert Decimal(result["equity_curve"][-1]["cash"]) == Decimal("10000") - notional


def test_limit_queue_fraction_caps_fills_at_the_daily_limit():
    script = ((MockOrderSignal("MOCK:ABC", "buy", Decimal("100"), "limit", Decimal("9")),),)
    bars = (
        _bar(0, close="10"),
        _bar(1, open_price="9.5", high="9.6", low="8.9", close="9.2", volume="1000"),
    )
    full = replay_script(
        script,
        bars,
        BacktestExecutionModel(
            lot_size=100,
            daily_price_limit_fraction=Decimal("0.1"),
            limit_queue_fraction=Decimal("1"),
        ),
    )
    assert full["fills"][0]["price"] == "9"

    capped = replay_script(
        script,
        bars,
        BacktestExecutionModel(
            lot_size=100,
            daily_price_limit_fraction=Decimal("0.1"),
            limit_queue_fraction=Decimal("0.00001"),
        ),
    )
    assert capped["fills"] == []
    assert capped["orders"][0]["status"] in {"rejected", "unfilled_end_of_data"}


def test_chunked_strategy_evaluation_replays_prefixes_and_aborts_early():
    import pytest

    from qtrade_mock.backtest_engine import ExecutionCancelled

    bar = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    bars = tuple(
        bar.model_copy(update={"timestamp": bar.timestamp + timedelta(minutes=index)})
        for index in range(6)
    )
    calls: list[int] = []

    def evaluate(prefix, parameters):
        calls.append(len(prefix))
        return tuple(() for _ in prefix)

    result = MockEngineAdapter._chunked_signals(evaluate, bars, {}, None)
    assert result == tuple(() for _ in bars)
    assert calls == [6]

    MockEngineAdapter._SIGNAL_CHUNK = 2
    try:
        calls.clear()
        MockEngineAdapter._chunked_signals(evaluate, bars, {}, None)
        assert calls == [2, 4, 6]

        calls.clear()
        with pytest.raises(ExecutionCancelled):
            MockEngineAdapter._chunked_signals(evaluate, bars, {}, lambda: len(calls) >= 1)
        assert calls == [2]
    finally:
        MockEngineAdapter._SIGNAL_CHUNK = 1000


def test_tick_prints_aggregate_into_execution_bars():
    from datetime import UTC, datetime

    template = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    base = datetime(2026, 8, 14, 1, 30, tzinfo=UTC)
    ticks = tuple(
        template.model_copy(
            update={
                "granularity": "tick",
                "timestamp": base + timedelta(seconds=index * 2),
                "open": Decimal(str(10 + index * 0.01)),
                "high": Decimal(str(10 + index * 0.01)),
                "low": Decimal(str(10 + index * 0.01)),
                "close": Decimal(str(10 + index * 0.01)),
                "volume": Decimal("100"),
                "value": Decimal("1000"),
            }
        )
        for index in range(6)
    )
    bars = MockEngineAdapter._aggregate_ticks(ticks, 4)
    assert len(bars) == 3
    assert bars[0].timestamp == base
    assert bars[0].open == Decimal("10")
    assert bars[0].close == Decimal("10.01")
    assert bars[0].volume == Decimal("200")
    assert bars[1].timestamp == base + timedelta(seconds=4)
    assert bars[2].granularity == "tick"


def test_vendor_factor_series_scales_prices_relative_to_run_start():
    from datetime import date

    from qtrade_data_contracts.research import AdjustmentFactor

    template = Bar.model_validate_json((FIXTURES / "Bar.json").read_text())
    instrument = "stock:CN:SH:600519"
    run = BacktestRun.model_validate_json((FIXTURES / "BacktestRun.json").read_text())
    bars = tuple(
        template.model_copy(
            update={
                "instrument_id": instrument,
                "timestamp": template.timestamp + timedelta(days=index),
                "open": Decimal("10"),
                "high": Decimal("10"),
                "low": Decimal("10"),
                "close": Decimal("10"),
            }
        )
        for index in range(3)
    )

    def factor(day: int, value: str) -> AdjustmentFactor:
        return AdjustmentFactor.model_validate(
            {
                "schema_version": 2,
                "scope_id": run.workspace_id,
                "source": "fixture",
                "instrument_id": instrument,
                "collected_at": datetime(2026, 1, 1, tzinfo=UTC),
                "mapping_version": "fixture.v1",
                "observation_date": date(2026, 10, 1) + timedelta(days=day),
                "adjustment": "backward",
                "factor": value,
                "base_date": date(2020, 1, 1),
                "base_value": "1",
                "algorithm_version": "fixture.v1",
                "convention_evidence": "fixture",
            }
        )

    adjusted = MockEngineAdapter._apply_adjustment_factors(
        bars,
        (factor(0, "1"), factor(1, "0.5")),
        run,
        BacktestExecutionModel(),
    )
    assert adjusted[0].close == Decimal("10")
    assert adjusted[1].close == Decimal("5")
    assert adjusted[2].close == Decimal("5")
