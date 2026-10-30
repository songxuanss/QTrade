-- Queue state is mutable; frozen plans, raw evidence and publications are untouched.
ALTER TABLE qtrade_data.research_job DROP CONSTRAINT research_job_state_check;
ALTER TABLE qtrade_data.research_job ADD CONSTRAINT research_job_state_check
    CHECK (state IN ('queued','running','waiting','completed','no_observations','failed'));
ALTER TABLE qtrade_data.research_job ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE qtrade_data.research_job ADD COLUMN wait_reason text
    CHECK (wait_reason IN ('source_daily','scope_daily','source_concurrency','scope_concurrency'));
ALTER TABLE qtrade_data.research_job ADD CONSTRAINT research_budget_wait_state
    CHECK ((wait_reason IS NULL OR (state IN ('waiting','failed')
                                   AND error_code='rate_limited')) IS TRUE);
CREATE INDEX research_job_wait_ready ON qtrade_data.research_job(next_attempt_at)
    WHERE state='waiting';
