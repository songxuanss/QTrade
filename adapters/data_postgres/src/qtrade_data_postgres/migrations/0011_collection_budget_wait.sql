-- Budget waits retain successful work and do not consume supplier failure retries.
ALTER TABLE qtrade_data.collection_part ADD COLUMN wait_reason text
    CHECK (wait_reason IN ('source_daily','scope_daily','source_concurrency','scope_concurrency'));
ALTER TABLE qtrade_data.collection_part ADD CONSTRAINT collection_budget_wait_state
    CHECK (wait_reason IS NULL OR (state IN ('waiting','failed') AND error_code='rate_limited'));
