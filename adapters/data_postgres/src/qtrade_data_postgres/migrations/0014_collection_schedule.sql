-- Separate collector-owned plans; existing QTrade calendar revisions are untouched.
CREATE TABLE qtrade_data.collection_schedule (
    scope_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    draft jsonb NOT NULL CHECK (jsonb_typeof(draft)='object'),
    revision text NOT NULL CHECK (revision ~ '^[a-f0-9]{64}$'),
    state text NOT NULL DEFAULT 'disabled' CHECK (state IN ('disabled','enabled','paused')),
    pause_reason text,
    next_due_at timestamptz NOT NULL,
    lease_id uuid,
    lease_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,schedule_id),
    CHECK ((state='paused' AND pause_reason IN
        ('authorization','source_unavailable','approval_required')) IS TRUE
        OR (state IN ('enabled','disabled') AND pause_reason IS NULL)),
    CHECK ((lease_id IS NULL) = (lease_until IS NULL)),
    CHECK ((draft->>'scope_id'=scope_id::text AND draft->>'schedule_id'=schedule_id::text) IS TRUE)
);
CREATE INDEX collection_schedule_due ON qtrade_data.collection_schedule(next_due_at)
    WHERE state='enabled';

-- Reservation is a dispatch identity, not a successful download.
CREATE TABLE qtrade_data.collection_schedule_run (
    scope_id uuid NOT NULL,
    run_id uuid NOT NULL,
    schedule_id uuid NOT NULL,
    revision text NOT NULL CHECK (revision ~ '^[a-f0-9]{64}$'),
    due_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,run_id),
    UNIQUE(scope_id,schedule_id,revision,due_at),
    FOREIGN KEY(scope_id,schedule_id) REFERENCES qtrade_data.collection_schedule(scope_id,schedule_id)
);
CREATE FUNCTION qtrade_data.reject_schedule_run_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'schedule run identities are immutable';
END;
$$;
CREATE TRIGGER schedule_run_no_change BEFORE UPDATE OR DELETE
    ON qtrade_data.collection_schedule_run FOR EACH ROW
    EXECUTE FUNCTION qtrade_data.reject_schedule_run_change();
CREATE TRIGGER schedule_run_no_truncate BEFORE TRUNCATE
    ON qtrade_data.collection_schedule_run FOR EACH STATEMENT
    EXECUTE FUNCTION qtrade_data.reject_schedule_run_change();
