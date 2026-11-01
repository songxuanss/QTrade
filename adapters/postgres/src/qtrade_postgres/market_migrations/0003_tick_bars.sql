-- Published trade-tick datasets carry one row per observed print; backtests
-- aggregate them into execution buckets.
ALTER TABLE market_bar DROP CONSTRAINT market_bar_granularity_check;
ALTER TABLE market_bar ADD CONSTRAINT market_bar_granularity_check
    CHECK (granularity IN ('tick','1m','5m','15m','30m','1h','1d'));
