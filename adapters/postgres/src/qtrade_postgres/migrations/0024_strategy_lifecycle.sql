CREATE TABLE strategy_version_lifecycle (
    workspace_id uuid NOT NULL,
    strategy_id uuid NOT NULL,
    version integer NOT NULL,
    state text NOT NULL CHECK (state IN (
        'draft','validated','backtested','paper','approved','live','paused','failed','archived'
    )),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id,strategy_id,version),
    FOREIGN KEY (workspace_id,strategy_id,version)
        REFERENCES strategy_version(workspace_id,strategy_id,version) ON DELETE RESTRICT
);

ALTER TABLE strategy_version
    ADD CONSTRAINT strategy_version_identity_uq
    UNIQUE (workspace_id,strategy_id,version,strategy_version_id);

CREATE TABLE strategy_state_transition (
    transition_id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL,
    strategy_id uuid NOT NULL,
    strategy_version_id uuid NOT NULL,
    version integer NOT NULL,
    from_state text NOT NULL CHECK (from_state IN (
        'draft','validated','backtested','paper','approved','live','paused','failed','archived'
    )),
    to_state text NOT NULL CHECK (to_state IN (
        'draft','validated','backtested','paper','approved','live','paused','failed','archived'
    )),
    actor_id uuid NOT NULL REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    occurred_at timestamptz NOT NULL,
    reason text NOT NULL DEFAULT '' CHECK (length(reason) <= 512 AND reason !~ E'[\\r\\n]'),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(evidence)='object'),
    CHECK (from_state <> to_state),
    FOREIGN KEY (workspace_id,strategy_id,version,strategy_version_id)
        REFERENCES strategy_version(workspace_id,strategy_id,version,strategy_version_id)
        ON DELETE RESTRICT
);
CREATE INDEX strategy_state_transition_history_idx
    ON strategy_state_transition(workspace_id,strategy_id,version,occurred_at,transition_id);

CREATE TABLE strategy_version_approval (
    approval_id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL,
    strategy_id uuid NOT NULL,
    strategy_version_id uuid NOT NULL,
    version integer NOT NULL,
    approver_id uuid NOT NULL REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    approved_at timestamptz NOT NULL,
    backtest_run_id uuid NOT NULL,
    paper_observations integer NOT NULL CHECK (paper_observations >= 1),
    reason text NOT NULL DEFAULT '' CHECK (length(reason) <= 512 AND reason !~ E'[\\r\\n]'),
    UNIQUE (workspace_id,strategy_id,version),
    FOREIGN KEY (workspace_id,strategy_id,version,strategy_version_id)
        REFERENCES strategy_version(workspace_id,strategy_id,version,strategy_version_id)
        ON DELETE RESTRICT
);

CREATE FUNCTION reject_strategy_lifecycle_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'strategy lifecycle history is append-only' USING ERRCODE = '55000';
END $$;
CREATE TRIGGER strategy_state_transition_no_update_delete
    BEFORE UPDATE OR DELETE ON strategy_state_transition
    FOR EACH ROW EXECUTE FUNCTION reject_strategy_lifecycle_history_mutation();
CREATE TRIGGER strategy_state_transition_no_truncate
    BEFORE TRUNCATE ON strategy_state_transition
    FOR EACH STATEMENT EXECUTE FUNCTION reject_strategy_lifecycle_history_mutation();
CREATE TRIGGER strategy_version_approval_no_update_delete
    BEFORE UPDATE OR DELETE ON strategy_version_approval
    FOR EACH ROW EXECUTE FUNCTION reject_strategy_lifecycle_history_mutation();
CREATE TRIGGER strategy_version_approval_no_truncate
    BEFORE TRUNCATE ON strategy_version_approval
    FOR EACH STATEMENT EXECUTE FUNCTION reject_strategy_lifecycle_history_mutation();
