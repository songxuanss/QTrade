CREATE TABLE reconciliation_report (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    report_id uuid NOT NULL,
    account_id uuid NOT NULL,
    reconciled_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('matched','mismatch')),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    PRIMARY KEY (workspace_id,report_id),
    UNIQUE (workspace_id,account_id,report_id),
    CHECK ((payload->>'workspace_id')::uuid=workspace_id),
    CHECK ((payload->>'report_id')::uuid=report_id),
    CHECK ((payload->>'account_id')::uuid=account_id),
    CHECK ((payload->>'reconciled_at')::timestamptz=reconciled_at),
    CHECK (payload->>'status'=status),
    CHECK (jsonb_typeof(payload->'differences')='array'),
    CHECK ((status='matched')=(jsonb_array_length(payload->'differences')=0))
);
CREATE INDEX reconciliation_report_latest_idx
    ON reconciliation_report(workspace_id,account_id,reconciled_at DESC,report_id);
CREATE FUNCTION reject_reconciliation_report_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'reconciliation reports are append-only' USING ERRCODE='55000';
END $$;
CREATE TRIGGER reconciliation_report_no_update_delete
    BEFORE UPDATE OR DELETE ON reconciliation_report
    FOR EACH ROW EXECUTE FUNCTION reject_reconciliation_report_mutation();
CREATE TRIGGER reconciliation_report_no_truncate
    BEFORE TRUNCATE ON reconciliation_report
    FOR EACH STATEMENT EXECUTE FUNCTION reject_reconciliation_report_mutation();

CREATE TABLE live_account_gate (
    workspace_id uuid NOT NULL,
    account_id uuid NOT NULL,
    report_id uuid NOT NULL,
    blocked_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id,account_id),
    FOREIGN KEY (workspace_id,account_id,report_id)
        REFERENCES reconciliation_report(workspace_id,account_id,report_id) ON DELETE RESTRICT
);
CREATE FUNCTION require_mismatched_reconciliation_gate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM reconciliation_report
        WHERE workspace_id=NEW.workspace_id AND account_id=NEW.account_id
          AND report_id=NEW.report_id AND status='mismatch'
    ) THEN
        RAISE EXCEPTION 'only a reconciliation mismatch can block an account' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER live_account_gate_requires_mismatch BEFORE INSERT OR UPDATE ON live_account_gate
    FOR EACH ROW EXECUTE FUNCTION require_mismatched_reconciliation_gate();
CREATE FUNCTION validate_live_account_gate_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'live account gate requires controlled recovery' USING ERRCODE='55000';
    END IF;
    IF OLD.workspace_id<>NEW.workspace_id OR OLD.account_id<>NEW.account_id
       OR NEW.blocked_at<OLD.blocked_at THEN
        RAISE EXCEPTION 'invalid live account gate update' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER live_account_gate_validate_update BEFORE UPDATE OR DELETE ON live_account_gate
    FOR EACH ROW EXECUTE FUNCTION validate_live_account_gate_update();
