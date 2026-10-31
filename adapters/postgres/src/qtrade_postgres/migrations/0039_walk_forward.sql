-- Rolling out-of-sample walk-forward batches; each window owns a real backtest run.
CREATE TABLE backtest_walk_forward (
    workspace_id uuid NOT NULL,
    walk_forward_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    created_by uuid NOT NULL REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    strategy_id uuid NOT NULL,
    strategy_version integer NOT NULL CHECK (strategy_version >= 1),
    strategy_version_id uuid NOT NULL,
    dataset_id uuid NOT NULL,
    instrument_id text NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (workspace_id, walk_forward_id),
    FOREIGN KEY (workspace_id, strategy_id, strategy_version, strategy_version_id)
        REFERENCES strategy_version(workspace_id, strategy_id, version, strategy_version_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id, dataset_id)
        REFERENCES market_dataset(workspace_id, dataset_id) ON DELETE RESTRICT,
    CHECK ((payload->>'workspace_id') IS NOT DISTINCT FROM workspace_id::text),
    CHECK ((payload->>'walk_forward_id') IS NOT DISTINCT FROM walk_forward_id::text),
    CHECK ((payload->>'dataset_id') IS NOT DISTINCT FROM dataset_id::text),
    CHECK ((payload->>'instrument_id') IS NOT DISTINCT FROM instrument_id)
);

CREATE INDEX backtest_walk_forward_recent_idx
    ON backtest_walk_forward(workspace_id, created_at DESC);
