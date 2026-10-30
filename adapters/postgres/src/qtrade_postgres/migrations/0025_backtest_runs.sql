CREATE TABLE backtest_run (
    workspace_id uuid NOT NULL,
    run_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    created_by uuid NOT NULL REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    strategy_id uuid NOT NULL,
    strategy_version integer NOT NULL CHECK (strategy_version >= 1),
    strategy_version_id uuid NOT NULL,
    dataset_id uuid NOT NULL,
    manifest_id uuid NOT NULL UNIQUE,
    engine_job_id uuid,
    revision integer NOT NULL CHECK (revision >= 1),
    status text NOT NULL CHECK (status IN ('queued','running','cancelled','failed','completed')),
    progress integer NOT NULL CHECK (progress BETWEEN 0 AND 100),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    PRIMARY KEY (workspace_id,run_id),
    FOREIGN KEY (workspace_id,strategy_id,strategy_version,strategy_version_id)
        REFERENCES strategy_version(workspace_id,strategy_id,version,strategy_version_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id,dataset_id)
        REFERENCES market_dataset(workspace_id,dataset_id) ON DELETE RESTRICT,
    CHECK ((payload->>'workspace_id') IS NOT DISTINCT FROM workspace_id::text),
    CHECK ((payload->>'run_id') IS NOT DISTINCT FROM run_id::text),
    CHECK ((payload->>'strategy_version_id') IS NOT DISTINCT FROM strategy_version_id::text),
    CHECK ((payload->>'dataset_id') IS NOT DISTINCT FROM dataset_id::text),
    CHECK ((payload->>'manifest_id') IS NOT DISTINCT FROM manifest_id::text),
    CHECK ((payload->>'status') IS NOT DISTINCT FROM status),
    CHECK ((payload->>'progress')::integer IS NOT DISTINCT FROM progress),
    CHECK (status <> 'completed' OR (progress=100 AND payload->>'result_object_key' IS NOT NULL)),
    CHECK (status <> 'failed' OR payload->>'error_code' IS NOT NULL)
);
CREATE INDEX backtest_run_workspace_created_idx
    ON backtest_run(workspace_id,created_at DESC,run_id);
CREATE INDEX backtest_run_active_idx ON backtest_run(status,workspace_id) WHERE status IN ('queued','running');

CREATE FUNCTION validate_backtest_run_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.payload - ARRAY['status','progress','result_object_key','error_code'])
            IS DISTINCT FROM
       (NEW.payload - ARRAY['status','progress','result_object_key','error_code'])
        OR OLD.workspace_id<>NEW.workspace_id
        OR OLD.run_id<>NEW.run_id
        OR OLD.created_at<>NEW.created_at
        OR OLD.created_by<>NEW.created_by
        OR OLD.strategy_id<>NEW.strategy_id
        OR OLD.strategy_version<>NEW.strategy_version
        OR OLD.strategy_version_id<>NEW.strategy_version_id
        OR OLD.dataset_id<>NEW.dataset_id
        OR OLD.manifest_id<>NEW.manifest_id THEN
        RAISE EXCEPTION 'backtest run inputs are immutable' USING ERRCODE='55000';
    END IF;
    IF OLD.status IN ('cancelled','failed','completed') AND NEW.payload IS DISTINCT FROM OLD.payload THEN
        RAISE EXCEPTION 'terminal backtest run is immutable' USING ERRCODE='55000';
    END IF;
    IF NEW.progress<OLD.progress OR NEW.revision<>OLD.revision+1 THEN
        RAISE EXCEPTION 'invalid backtest run progress update' USING ERRCODE='55000';
    END IF;
    IF OLD.engine_job_id IS NOT NULL AND NEW.engine_job_id IS DISTINCT FROM OLD.engine_job_id THEN
        RAISE EXCEPTION 'backtest engine job identity is immutable' USING ERRCODE='55000';
    END IF;
    IF OLD.payload->>'result_object_key' IS NOT NULL
        AND NEW.payload->>'result_object_key' IS DISTINCT FROM OLD.payload->>'result_object_key'
        OR NEW.status<>'completed' AND NEW.payload->>'result_object_key' IS NOT NULL THEN
        RAISE EXCEPTION 'backtest result reference is immutable' USING ERRCODE='55000';
    END IF;
    IF OLD.status='running' AND NEW.status='queued'
        OR OLD.status IN ('cancelled','failed','completed') AND NEW.status<>OLD.status THEN
        RAISE EXCEPTION 'invalid backtest run state transition' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER backtest_run_validate_update BEFORE UPDATE ON backtest_run
    FOR EACH ROW EXECUTE FUNCTION validate_backtest_run_update();

CREATE TABLE backtest_run_event (
    event_id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL,
    run_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    status text NOT NULL CHECK (status IN ('queued','running','cancelled','failed','completed')),
    progress integer NOT NULL CHECK (progress BETWEEN 0 AND 100),
    occurred_at timestamptz NOT NULL,
    error_code text,
    UNIQUE (workspace_id,run_id,revision),
    FOREIGN KEY (workspace_id,run_id) REFERENCES backtest_run(workspace_id,run_id)
        ON DELETE RESTRICT
);
CREATE INDEX backtest_run_event_history_idx
    ON backtest_run_event(workspace_id,run_id,revision);

CREATE FUNCTION reject_backtest_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'backtest run history is append-only' USING ERRCODE='55000';
END $$;
CREATE TRIGGER backtest_run_event_no_update_delete BEFORE UPDATE OR DELETE ON backtest_run_event
    FOR EACH ROW EXECUTE FUNCTION reject_backtest_event_mutation();
CREATE TRIGGER backtest_run_event_no_truncate BEFORE TRUNCATE ON backtest_run_event
    FOR EACH STATEMENT EXECUTE FUNCTION reject_backtest_event_mutation();
