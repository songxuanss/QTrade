CREATE TABLE risk_limit (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    limit_id text NOT NULL CHECK (length(limit_id) BETWEEN 1 AND 128),
    dimension text NOT NULL CHECK (dimension IN ('account','strategy','instrument','workspace')),
    scope_id text NOT NULL CHECK (length(scope_id) BETWEEN 1 AND 128),
    metric text NOT NULL CHECK (metric IN (
        'position_quantity','notional_exposure','order_frequency','daily_loss','drawdown','leverage'
    )),
    maximum numeric NOT NULL CHECK (maximum > 0),
    version text NOT NULL CHECK (length(version) BETWEEN 1 AND 128),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id,limit_id,version),
    UNIQUE (workspace_id,dimension,scope_id,metric,version)
);
CREATE INDEX risk_limit_active_scope_idx
    ON risk_limit(workspace_id,dimension,scope_id,metric) WHERE active;
CREATE FUNCTION reject_risk_limit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' AND OLD.active AND NOT NEW.active
        AND (to_jsonb(OLD) - 'active' - 'created_at')=(to_jsonb(NEW) - 'active' - 'created_at') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'risk limit versions are immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER risk_limit_no_update_delete BEFORE UPDATE OR DELETE ON risk_limit
    FOR EACH ROW EXECUTE FUNCTION reject_risk_limit_mutation();
CREATE TRIGGER risk_limit_no_truncate BEFORE TRUNCATE ON risk_limit
    FOR EACH STATEMENT EXECUTE FUNCTION reject_risk_limit_mutation();
