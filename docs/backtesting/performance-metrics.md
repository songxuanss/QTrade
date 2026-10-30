# Backtest performance metrics

`BacktestResult.metrics` is produced from the normalized equity curve and is
stored with the immutable result. Maximum drawdown uses the running equity peak.
CAGR uses elapsed UTC time between the first and last observations. Annualized
volatility, Sharpe, and Sortino require a frozen `periods_per_year` value; QTrade
does not infer a sampling frequency from irregular tick timestamps. The risk-free
rate is zero in this first version. Values that cannot be defined for the sample
are `null`, including volatility ratios with fewer than two returns or zero
variance. When the selected benchmark instrument is present in the frozen dataset,
benchmark return uses its first and last close inside the run window (buy and hold,
excluding benchmark transaction costs); otherwise it is `null`. Each equity point
also records marked positions, drawdown, and a benchmark equity series scaled to
the initial cash where aligned benchmark bars are available. Exposure is measured
at each emitted equity point and summarized with a simple mean and maximum.

Fills are matched FIFO by instrument to estimate realized trade quality. A completed
closing fill event is the trade-count unit; its realized PnL includes allocated
entry and exit fees. Turnover is total traded notional divided by initial cash.
Profit factor is left undefined until at least one losing close exists.

The report also exposes the canonical cash/equity curve, orders, and fills so the
UI can explain summary values and the underlying executions. A report endpoint
verifies the object-store checksum and confirms workspace, dataset, and strategy
version match the completed run before returning it.
