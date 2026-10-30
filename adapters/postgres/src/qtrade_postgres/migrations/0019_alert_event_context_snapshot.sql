ALTER TABLE alert_event
    ADD COLUMN context_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(context_snapshot)='object');
