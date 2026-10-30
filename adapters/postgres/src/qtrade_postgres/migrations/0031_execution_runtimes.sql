CREATE TABLE execution_runtime (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    runtime_id uuid NOT NULL,
    account_id uuid NOT NULL,
    strategy_version_id uuid NOT NULL,
    mode text NOT NULL CHECK (mode IN ('paper','live')),
    state text NOT NULL CHECK (state IN ('running','paused','stopped')),
    revision integer NOT NULL CHECK (revision >= 1),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id,runtime_id),
    FOREIGN KEY (strategy_version_id) REFERENCES strategy_version(strategy_version_id)
        ON DELETE RESTRICT
);
CREATE INDEX execution_runtime_workspace_state_idx
    ON execution_runtime(workspace_id,state,updated_at DESC,runtime_id);
