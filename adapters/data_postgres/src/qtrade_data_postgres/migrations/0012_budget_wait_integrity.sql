-- CHECK treats NULL as acceptable unless the predicate is explicitly true.
-- Keep already-applied 0011 checksum-locked and strengthen it additively.
ALTER TABLE qtrade_data.collection_part DROP CONSTRAINT collection_budget_wait_state;
ALTER TABLE qtrade_data.collection_part ADD CONSTRAINT collection_budget_wait_state
    CHECK ((wait_reason IS NULL OR (state IN ('waiting','failed')
                                  AND error_code='rate_limited')) IS TRUE);
