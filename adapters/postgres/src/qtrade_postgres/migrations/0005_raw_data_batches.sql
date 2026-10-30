-- Metadata becomes visible only after the immutable raw object is verified.
CREATE TABLE raw_data_batch (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id),
    batch_id uuid NOT NULL,
    source text NOT NULL CHECK(length(source) BETWEEN 1 AND 256),
    ingestion_task_id uuid NOT NULL,
    content_sha256 char(64) NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
    published_by uuid NOT NULL REFERENCES qtrade_user(user_id),
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(workspace_id,batch_id),
    CHECK(payload->>'workspace_id'=workspace_id::text),
    CHECK(payload->>'batch_id'=batch_id::text),
    CHECK(payload->>'content_sha256'=content_sha256)
);
CREATE INDEX raw_batch_collection_task ON raw_data_batch(workspace_id,ingestion_task_id);
CREATE INDEX raw_batch_source ON raw_data_batch(workspace_id,source,published_at);
CREATE FUNCTION reject_raw_data_batch_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'raw batch metadata is immutable';
END;
$$;
CREATE TRIGGER raw_batch_no_change BEFORE UPDATE OR DELETE ON raw_data_batch
    FOR EACH ROW EXECUTE FUNCTION reject_raw_data_batch_change();
CREATE TRIGGER raw_batch_no_truncate BEFORE TRUNCATE ON raw_data_batch
    FOR EACH STATEMENT EXECUTE FUNCTION reject_raw_data_batch_change();
