CREATE TABLE ingestion_task (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id),
    task_id uuid NOT NULL,
    created_by uuid NOT NULL REFERENCES qtrade_user(user_id),
    request jsonb NOT NULL CHECK(jsonb_typeof(request)='object'),
    status text NOT NULL CHECK(status IN ('queued','running','waiting','failed','completed')),
    next_attempt_at timestamptz NOT NULL,
    provider_cursor text CHECK(provider_cursor IS NULL OR length(provider_cursor) BETWEEN 1 AND 2048),
    batch_ids uuid[] NOT NULL DEFAULT '{}',
    seen_cursor_hashes text[] NOT NULL DEFAULT '{}',
    pending_page jsonb,
    collection_complete boolean NOT NULL DEFAULT false,
    failures integer NOT NULL DEFAULT 0 CHECK(failures>=0),
    last_error_code text,
    dataset_id uuid,
    lease_token uuid,
    lease_actor uuid REFERENCES qtrade_user(user_id),
    lease_worker uuid,
    lease_expires_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(workspace_id,task_id),
    CHECK((lease_token IS NULL AND lease_actor IS NULL AND lease_worker IS NULL
           AND lease_expires_at IS NULL) OR
          (lease_token IS NOT NULL AND lease_actor IS NOT NULL AND lease_worker IS NOT NULL
           AND lease_expires_at IS NOT NULL AND status='running')),
    CHECK((status='completed')=(dataset_id IS NOT NULL)),
    CHECK(last_error_code IS NULL OR last_error_code IN
          ('not_found','conflict','invalid_input','unsupported','unavailable','timeout',
           'rate_limited','unauthorized','checksum_mismatch','rejected')),
    CHECK(NOT collection_complete OR (cardinality(batch_ids)>0 AND provider_cursor IS NULL AND pending_page IS NULL)),
    CHECK((request->>'workspace_id') IS NOT DISTINCT FROM workspace_id::text),
    CHECK((request->>'task_id') IS NOT DISTINCT FROM task_id::text)
);
CREATE INDEX ingestion_due ON ingestion_task(status,next_attempt_at,updated_at)
    WHERE status IN ('queued','waiting','running');
CREATE FUNCTION validate_ingestion_task_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.workspace_id<>OLD.workspace_id OR NEW.task_id<>OLD.task_id
        OR NEW.created_by<>OLD.created_by OR NEW.request IS DISTINCT FROM OLD.request THEN
        RAISE EXCEPTION 'ingestion task identity is immutable';
    END IF;
    IF cardinality(NEW.batch_ids)<cardinality(OLD.batch_ids)
        OR NEW.batch_ids[1:cardinality(OLD.batch_ids)] IS DISTINCT FROM OLD.batch_ids THEN
        RAISE EXCEPTION 'ingestion checkpoints cannot move backward';
    END IF;
    IF cardinality(NEW.seen_cursor_hashes)<cardinality(OLD.seen_cursor_hashes)
        OR NEW.seen_cursor_hashes[1:cardinality(OLD.seen_cursor_hashes)]
           IS DISTINCT FROM OLD.seen_cursor_hashes THEN
        RAISE EXCEPTION 'ingestion cursor history cannot move backward';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ingestion_task_guard BEFORE UPDATE ON ingestion_task
    FOR EACH ROW EXECUTE FUNCTION validate_ingestion_task_change();
CREATE FUNCTION reject_ingestion_task_removal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ingestion tasks are immutable';
END;
$$;
CREATE TRIGGER ingestion_task_no_delete BEFORE DELETE ON ingestion_task
    FOR EACH ROW EXECUTE FUNCTION reject_ingestion_task_removal();
CREATE TRIGGER ingestion_task_no_truncate BEFORE TRUNCATE ON ingestion_task
    FOR EACH STATEMENT EXECUTE FUNCTION reject_ingestion_task_removal();
CREATE TABLE ingestion_source_rate (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id),
    source text NOT NULL,
    authorization_id text NOT NULL,
    next_allowed_at timestamptz NOT NULL,
    PRIMARY KEY(workspace_id,source,authorization_id)
);
