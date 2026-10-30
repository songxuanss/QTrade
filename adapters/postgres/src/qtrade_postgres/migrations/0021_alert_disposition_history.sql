ALTER TABLE alert_event
    ADD COLUMN assignee_id uuid REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    ADD COLUMN silenced_until timestamptz;

CREATE TABLE alert_disposition_history (
    history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    event_id uuid NOT NULL REFERENCES alert_event(event_id) ON DELETE RESTRICT,
    actor_id uuid NOT NULL REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    action text NOT NULL CHECK (action IN ('claim','acknowledge','silence','close','note')),
    note text CHECK (note IS NULL OR (length(btrim(note)) BETWEEN 1 AND 2048)),
    silenced_until timestamptz,
    occurred_at timestamptz NOT NULL,
    CHECK ((action = 'silence') = (silenced_until IS NOT NULL)),
    CHECK (action <> 'note' OR note IS NOT NULL)
);
CREATE INDEX alert_disposition_event_time_idx
    ON alert_disposition_history (workspace_id,event_id,occurred_at,history_id);

CREATE FUNCTION reject_alert_disposition_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'alert disposition history is immutable' USING ERRCODE = '55000';
END $$;
CREATE TRIGGER alert_disposition_no_update_delete
    BEFORE UPDATE OR DELETE ON alert_disposition_history
    FOR EACH ROW EXECUTE FUNCTION reject_alert_disposition_mutation();
CREATE TRIGGER alert_disposition_no_truncate
    BEFORE TRUNCATE ON alert_disposition_history
    FOR EACH STATEMENT EXECUTE FUNCTION reject_alert_disposition_mutation();
