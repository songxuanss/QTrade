# Backtest execution assumptions

Backtest assumptions are frozen in `ArtifactManifest.execution_config_json` and
covered by its SHA-256. Engines must use the same values when replaying a run.
The local sandbox event engine accepts an optional `execution_model` object:

```json
{
  "lot_size": 100,
  "fee_bps": "3",
  "minimum_fee": "5",
  "sell_tax_bps": "5",
  "tick_size": "0.01",
  "settlement_days": 1,
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
down to the market lot before submission; the final fill after cash and volume
caps is rounded to the same lot, so partial fills cannot create untradeable odd
lots. Fill prices are rounded adversely to `tick_size`; `settlement_days=1`
prevents same-session resale of newly bought A-share quantity. Slippage is applied adversely by side, fees reduce cash, and both costs are
represented in normalized fills and the equity curve. Session times are
interpreted in `timezone_name`, excluding configured breaks such as the A-share
lunch interval; daily bars bypass the intraday session and fill on the next
daily bar. Price limits compare with the prior market day's close.
The price-limit check is skipped until a prior close exists inside the selected
dataset; include a prior session in the dataset when first-day limit behavior
matters.
Local deployments execute the stored Python strategy through the isolated
Runner over a Unix socket. The Runner receives one ordered bar series and calls
`on_bar` sequentially, so a strategy only sees current and prior bars. The model
does not yet implement corporate actions, historical universe membership, or
order-book impact. These assumptions
are user-editable and are frozen into each run's manifest; review
`QUANT-MODEL-READINESS.md` before treating a report as production-ready.
