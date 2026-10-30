CREATE TABLE alert_evaluation_state (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    rule_id uuid NOT NULL,
    active_since timestamptz,
    last_emitted_at timestamptz,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, rule_id)
);
