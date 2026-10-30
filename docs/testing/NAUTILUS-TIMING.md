# Nautilus event timing verification

The selected engine remains NautilusTrader (ADR 0004). The deployed backtest
pipeline has not yet been replaced with this engine; this probe establishes one
necessary matching assumption using the actual SDK and synthetic data.

Run the version-pinned development probe:

```sh
.tools/uv/bin/uv pip install --python .tools/nautilus-venv/bin/python nautilus_trader==1.231.0
.tools/nautilus-venv/bin/python scripts/verification-nautilus-timing.py
```

The separate ignored environment must already exist, or can be created with
`uv venv .tools/nautilus-venv`. It does not add the SDK to the API, Worker, or
existing untrusted-source sandbox.

Three synthetic daily bars have opening/closing prices 10/11, 20/21 and 30/31.
A fixed trusted strategy submits a 100-share market order in the first bar
callback. With default bar execution and a positive message latency, the actual
1.231.0 engine fills at the second bar's **close, 21**. It must not be assumed to
implement the QTrade next-open model merely because the event is a later bar.

The alternative disables native bar execution, supplies explicit synthetic open
events, and schedules the bar callback only after its interval. It fills at the
second bar's **open, 20**. Two independent engines must return identical fill
timestamp, price and quantity. Generated SDK order/trade IDs are not deterministic
business result fields and are excluded from this comparison.

This construction is a bar-model experiment, not real historical Tick data.
The final adapter must map close availability using the exchange session calendar
(including daily closes, lunch breaks and end-labelled historical data), account
for costs, volume and latency, and enforce supported A-share constraints. The
probe uses an artificial continuous UTC daily interval and does not validate
T+1, lot rounding after affordability, price limits or exchange sessions.

The SDK's observed import memory footprint exceeds the existing 256 MB strategy
sandbox. Keep untrusted strategy callbacks in the current restricted Runner and
design a separate bounded trusted engine execution profile. Do not increase or
disable untrusted-source limits just to import the SDK. Main-flow readiness still
requires immutable manifest replay, persisted real results, Worker consumption,
failure recovery and deployed desktop/mobile UI acceptance.
