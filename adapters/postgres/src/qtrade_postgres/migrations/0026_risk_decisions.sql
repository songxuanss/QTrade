CREATE TABLE risk_decision (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    decision_id uuid NOT NULL,
    intent_id uuid NOT NULL,
    decided_at timestamptz NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('allow','deny','require_approval')),
    input_sha256 char(64) NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    rule_set_version text NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    input_summary jsonb NOT NULL CHECK (jsonb_typeof(input_summary)='object'),
    PRIMARY KEY (workspace_id,decision_id),
    CHECK ((payload->>'workspace_id')::uuid=workspace_id),
    CHECK ((payload->>'decision_id')::uuid=decision_id),
    CHECK ((payload->>'intent_id')::uuid=intent_id),
    CHECK ((payload->>'decided_at')::timestamptz=decided_at),
    CHECK ((payload->>'outcome')=outcome),
    CHECK ((payload->>'input_sha256')=input_sha256),
    CHECK ((payload->>'rule_set_version')=rule_set_version)
);
CREATE INDEX risk_decision_workspace_time_idx
    ON risk_decision(workspace_id,decided_at DESC,decision_id);
CREATE INDEX risk_decision_workspace_outcome_idx
    ON risk_decision(workspace_id,outcome,decided_at DESC);
CREATE FUNCTION reject_risk_decision_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'risk decisions are append-only' USING ERRCODE='55000';
END $$;
CREATE TRIGGER risk_decision_no_update_delete BEFORE UPDATE OR DELETE ON risk_decision
    FOR EACH ROW EXECUTE FUNCTION reject_risk_decision_mutation();
CREATE TRIGGER risk_decision_no_truncate BEFORE TRUNCATE ON risk_decision
    FOR EACH STATEMENT EXECUTE FUNCTION reject_risk_decision_mutation();
