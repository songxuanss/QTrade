-- Allow an expired lease to return a running run to the queue without weakening
-- terminal immutability: only running -> queued is permitted, and only while the
-- lease is being cleared.
CREATE OR REPLACE FUNCTION validate_backtest_run_update() RETURNS trigger LANGUAGE plpgsql AS $$
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
    IF OLD.status='running' AND NEW.status='queued' THEN
        IF NEW.lease_owner IS NOT NULL OR NEW.lease_expires_at IS NOT NULL THEN
            RAISE EXCEPTION 'requeued backtest run must clear its lease' USING ERRCODE='55000';
        END IF;
    ELSIF NEW.status<>OLD.status AND OLD.status IN ('cancelled','failed','completed') THEN
        RAISE EXCEPTION 'invalid backtest run state transition' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
