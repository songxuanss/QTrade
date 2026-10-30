ALTER TABLE alert_rule_version
    ADD COLUMN notification_targets jsonb NOT NULL
    DEFAULT '[{"channel":"in_app","destination_id":"workspace"}]'::jsonb
    CHECK (jsonb_typeof(notification_targets)='array');
