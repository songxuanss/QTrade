-- Expand-only master data. Imported specifications are immutable.
CREATE TABLE instrument_specification (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id),
    instrument_id text NOT NULL CHECK (length(instrument_id) BETWEEN 1 AND 256),
    specification_version integer NOT NULL CHECK (specification_version > 0),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    name_search text NOT NULL,
    imported_by uuid NOT NULL REFERENCES qtrade_user(user_id),
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, instrument_id, specification_version)
);
CREATE TABLE instrument_current (
    workspace_id uuid NOT NULL,
    instrument_id text NOT NULL,
    specification_version integer NOT NULL,
    PRIMARY KEY (workspace_id, instrument_id),
    FOREIGN KEY (workspace_id, instrument_id, specification_version)
        REFERENCES instrument_specification(workspace_id, instrument_id, specification_version)
);
CREATE TABLE instrument_specification_conflict (
    issue_id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL,
    instrument_id text NOT NULL,
    existing_version integer NOT NULL,
    proposed jsonb NOT NULL CHECK (jsonb_typeof(proposed) = 'object'),
    proposal_sha256 char(64) NOT NULL CHECK (proposal_sha256 ~ '^[0-9a-f]{64}$'),
    detected_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'pending' CHECK (status = 'pending'),
    UNIQUE (workspace_id, instrument_id, proposal_sha256),
    FOREIGN KEY (workspace_id, instrument_id, existing_version)
        REFERENCES instrument_specification(workspace_id, instrument_id, specification_version)
);
CREATE INDEX instrument_conflict_workspace ON instrument_specification_conflict(workspace_id, detected_at);

CREATE FUNCTION reject_instrument_specification_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'instrument specifications are immutable';
END;
$$;
CREATE TRIGGER instrument_specification_no_change BEFORE UPDATE OR DELETE ON instrument_specification
    FOR EACH ROW EXECUTE FUNCTION reject_instrument_specification_change();
CREATE TRIGGER instrument_specification_no_truncate BEFORE TRUNCATE ON instrument_specification
    FOR EACH STATEMENT EXECUTE FUNCTION reject_instrument_specification_change();
