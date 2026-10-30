"""Real SDK timing probe using synthetic bars, never user strategy source.

Run with the separate Nautilus evaluation environment. This is not a deployed
engine, market-rule, Tick fidelity, or end-to-end readiness test.
"""

from decimal import Decimal
from importlib.metadata import version


def replay(*, explicit_open_events: bool) -> tuple[tuple[int, str, str], ...]:
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import LatencyModel
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model.data import Bar, BarType, TradeTick
    from nautilus_trader.model.enums import AccountType, AggressorSide, OmsType, OrderSide
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
    from nautilus_trader.model.instruments import Equity
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity
    from nautilus_trader.trading.strategy import Strategy

    instrument_id = InstrumentId.from_str("600519.SH")
    bar_type = BarType.from_str("600519.SH-1-DAY-LAST-EXTERNAL")
    fills: list[tuple[int, str, str]] = []
    start = 1735689600000000000
    day = 86400000000000

    class TimingProbe(Strategy):
        def on_start(self):
            self.count = 0
            self.subscribe_bars(bar_type)

        def on_bar(self, bar):
            self.count += 1
            if self.count == 1:
                self.submit_order(
                    self.order_factory.market(
                        instrument_id, OrderSide.BUY, Quantity.from_int(100)
                    )
                )

        def on_order_filled(self, event):
            # SDK-generated IDs are deliberately excluded from canonical facts.
            fills.append((event.ts_event, str(event.last_px), str(event.last_qty)))

    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True))
    )
    try:
        engine.add_venue(
            Venue("SH"),
            OmsType.NETTING,
            AccountType.CASH,
            [Money(Decimal("1000000"), Currency.from_str("CNY"))],
            base_currency=Currency.from_str("CNY"),
            latency_model=LatencyModel(base_latency_nanos=1),
            bar_execution=not explicit_open_events,
        )
        engine.add_instrument(
            Equity(
                instrument_id, Symbol("600519"), Currency.from_str("CNY"), 2,
                Price.from_str("0.01"), Quantity.from_int(100), 0, 0,
            )
        )
        prices = (("10.00", "11.00"), ("20.00", "21.00"), ("30.00", "31.00"))
        bars = []
        ticks = []
        for index, (opening, closing) in enumerate(prices):
            open_time = start + index * day
            # Source bars are start-labelled; callbacks cannot see a close at open.
            close_time = open_time + day - 1 if explicit_open_events else open_time
            bars.append(
                Bar(
                    bar_type, Price.from_str(opening), Price.from_str(closing),
                    Price.from_str(opening), Price.from_str(closing),
                    Quantity.from_int(10000), close_time, close_time,
                )
            )
            ticks.append(
                TradeTick(
                    instrument_id, Price.from_str(opening), Quantity.from_int(10000),
                    AggressorSide.NO_AGGRESSOR, TradeId(str(index)), open_time, open_time,
                )
            )
        engine.add_data(bars)
        if explicit_open_events:
            engine.add_data(ticks)
        engine.add_strategy(TimingProbe())
        engine.run()
        return tuple(fills)
    finally:
        engine.dispose()


def main() -> None:
    if version("nautilus_trader") != "1.231.0":
        raise SystemExit("timing probe requires NautilusTrader 1.231.0")
    next_open_time = 1735776000000000000
    default = replay(explicit_open_events=False)
    causal = replay(explicit_open_events=True)
    repeated = replay(explicit_open_events=True)
    if default != ((next_open_time, "21.00", "100"),):
        raise SystemExit("default bar matching behavior changed; review adapter assumptions")
    if causal != ((next_open_time, "20.00", "100"),) or repeated != causal:
        raise SystemExit("explicit open-event matching was noncausal or not repeatable")
    print("PASS: actual Nautilus SDK, next-open matching and repeatable canonical fills")
    print("Scope: synthetic bars; no deployed engine, real Tick replay or A-share rule readiness")


if __name__ == "__main__":
    main()
