# Backtest execution assumptions

Backtest assumptions are frozen in `ArtifactManifest.execution_config_json` and
covered by its SHA-256. Engines must use the same values when replaying a run.
The mock engine accepts an optional `execution_model` object:

```json
{
  "lot_size": 100,
  "fee_bps": "3",
  "slippage_bps": "5",
  "max_volume_fraction": "0.1",
  "latency_bars": 1,
  "session_open": "09:30:00",
  "session_close": "15:00:00",
  "session_breaks": [["11:30:00", "13:00:00"]],
  "timezone_name": "Asia/Shanghai",
  "daily_price_limit_fraction": "0.1"
}
```

Signals are eligible after the configured number of bars. Quantities are rounded
down to the market lot before submission; fills may be partial up to the configured
share-volume participation. Slippage is applied adversely by side, fees reduce cash,
and both costs are represented in normalized fills and the equity curve. Session
times are interpreted in `timezone_name`, excluding configured breaks such as the
A-share lunch interval; price limits compare with the prior market day's close.
The price-limit check is skipped until a prior close exists inside the selected
dataset; include a prior session in the dataset when first-day limit behavior
matters.
These assumptions are user-editable and are frozen into each run's manifest. The
base model deliberately exposes its assumptions for markets
whose rules differ and should be extended with a versioned market calendar where
split sessions, instrument-specific lots, or special price limits are required.
