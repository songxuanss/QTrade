ALTER TABLE alert_evaluation_state
    ADD COLUMN last_emitted_severity text
    CHECK (last_emitted_severity IN ('info', 'warning', 'critical'));
