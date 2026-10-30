ALTER TABLE market_bar DROP CONSTRAINT IF EXISTS market_bar_granularity_check;
ALTER TABLE market_bar ADD CONSTRAINT market_bar_granularity_check CHECK(granularity IN ('1m','5m','15m','30m','1h','1d'));
