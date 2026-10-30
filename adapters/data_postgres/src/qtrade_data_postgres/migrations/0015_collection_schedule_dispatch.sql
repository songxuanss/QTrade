-- Append only. A reservation alone never proves a frozen input or queued job.
CREATE TABLE qtrade_data.collection_schedule_dispatch (
    scope_id uuid NOT NULL,
    run_id uuid NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,run_id),
    FOREIGN KEY(scope_id,run_id)
        REFERENCES qtrade_data.collection_schedule_run(scope_id,run_id),
    CHECK ((payload->>'scope_id'=scope_id::text AND payload->>'run_id'=run_id::text
        AND payload->>'schema_version'='1'
        AND payload->'dispatch'->>'kind' IN ('market','research')
        AND payload->'dispatch'->'request'->>'scope_id'=scope_id::text
        AND payload->'dispatch'->'request'->>'request_id'=run_id::text
        AND payload->'dispatch'->'request'->>'correlation_id'=run_id::text
        AND payload->'dispatch'->'request'->>'mode'='missing') IS TRUE)
);
CREATE TRIGGER schedule_dispatch_no_change BEFORE UPDATE OR DELETE
    ON qtrade_data.collection_schedule_dispatch FOR EACH ROW
    EXECUTE FUNCTION qtrade_data.reject_schedule_run_change();
CREATE TRIGGER schedule_dispatch_no_truncate BEFORE TRUNCATE
    ON qtrade_data.collection_schedule_dispatch FOR EACH STATEMENT
    EXECUTE FUNCTION qtrade_data.reject_schedule_run_change();
