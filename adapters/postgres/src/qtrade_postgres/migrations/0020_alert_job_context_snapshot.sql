ALTER TABLE alert_evaluation_job
    ADD COLUMN context_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(context_snapshot)='object');
