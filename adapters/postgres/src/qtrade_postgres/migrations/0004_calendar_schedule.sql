-- Immutable, workspace-scoped calendar revisions; no assumed exchange schedule.
CREATE TABLE calendar_schedule (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id),
    calendar_id text NOT NULL CHECK (length(calendar_id) BETWEEN 1 AND 256),
    calendar_version integer NOT NULL CHECK (calendar_version > 0),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    imported_by uuid NOT NULL REFERENCES qtrade_user(user_id),
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, calendar_id, calendar_version)
);
CREATE FUNCTION reject_calendar_schedule_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'calendar revisions are immutable';
END;
$$;
CREATE TRIGGER calendar_schedule_no_change BEFORE UPDATE OR DELETE ON calendar_schedule
    FOR EACH ROW EXECUTE FUNCTION reject_calendar_schedule_change();
CREATE TRIGGER calendar_schedule_no_truncate BEFORE TRUNCATE ON calendar_schedule
    FOR EACH STATEMENT EXECUTE FUNCTION reject_calendar_schedule_change();
