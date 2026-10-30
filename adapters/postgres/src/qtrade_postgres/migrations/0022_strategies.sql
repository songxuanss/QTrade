CREATE TABLE strategy (
    strategy_id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 2000),
    created_by uuid NOT NULL REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= created_at),
    current_version integer NOT NULL CHECK (current_version >= 1),
    archived boolean NOT NULL DEFAULT false,
    UNIQUE (strategy_id,workspace_id)
);
CREATE INDEX strategy_workspace_updated_idx ON strategy (workspace_id,updated_at DESC,strategy_id);

CREATE TABLE strategy_version (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    strategy_id uuid NOT NULL,
    strategy_version_id uuid NOT NULL UNIQUE,
    version integer NOT NULL CHECK (version >= 1),
    created_by uuid NOT NULL REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL,
    code_object_key text NOT NULL CHECK (length(code_object_key) BETWEEN 1 AND 256),
    code_sha256 text NOT NULL CHECK (code_sha256 ~ '^[0-9a-f]{64}$'),
    parameter_schema jsonb NOT NULL CHECK (jsonb_typeof(parameter_schema)='object'),
    dependencies_sha256 text NOT NULL CHECK (dependencies_sha256 ~ '^[0-9a-f]{64}$'),
    dependencies_object_key text NOT NULL CHECK (length(dependencies_object_key) BETWEEN 1 AND 256),
    PRIMARY KEY (workspace_id,strategy_id,version),
    FOREIGN KEY (strategy_id,workspace_id) REFERENCES strategy(strategy_id,workspace_id) ON DELETE RESTRICT
);
CREATE INDEX strategy_version_history_idx ON strategy_version (workspace_id,strategy_id,version);

CREATE FUNCTION reject_strategy_version_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'strategy versions are immutable' USING ERRCODE = '55000';
END $$;
CREATE TRIGGER strategy_version_no_update_delete
    BEFORE UPDATE OR DELETE ON strategy_version
    FOR EACH ROW EXECUTE FUNCTION reject_strategy_version_mutation();
CREATE TRIGGER strategy_version_no_truncate
    BEFORE TRUNCATE ON strategy_version
    FOR EACH STATEMENT EXECUTE FUNCTION reject_strategy_version_mutation();
