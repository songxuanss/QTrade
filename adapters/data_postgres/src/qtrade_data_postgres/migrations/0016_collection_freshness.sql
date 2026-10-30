-- Observations are immutable history, not a mutable "healthy" cache. Newer
-- checked_at determines the latest snapshot even if an old probe finishes late.
CREATE TABLE qtrade_data.collection_freshness_snapshot (
    scope_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    key_sha256 text NOT NULL CHECK (key_sha256 ~ '^[a-f0-9]{64}$'),
    checked_at timestamptz NOT NULL,
    result_sha256 text NOT NULL CHECK (result_sha256 ~ '^[a-f0-9]{64}$'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    stored_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,snapshot_id),
    UNIQUE(scope_id,key_sha256,checked_at),
    CHECK ((payload->>'scope_id'=scope_id::text
        AND payload->'key'->>'scope_id'=scope_id::text
        AND payload->'evidence'->'key'=payload->'key'
        AND payload->'schema_version'='1'::jsonb
        AND jsonb_typeof(payload->'checked_at')='string'
        AND (payload->>'checked_at')::timestamptz=checked_at) IS TRUE)
);
CREATE INDEX collection_freshness_latest ON qtrade_data.collection_freshness_snapshot
    (scope_id,key_sha256,checked_at DESC);
CREATE FUNCTION qtrade_data.reject_freshness_snapshot_change() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'freshness snapshots are immutable';
END;
$$;
CREATE TRIGGER freshness_snapshot_no_change BEFORE UPDATE OR DELETE
    ON qtrade_data.collection_freshness_snapshot FOR EACH ROW
    EXECUTE FUNCTION qtrade_data.reject_freshness_snapshot_change();
CREATE TRIGGER freshness_snapshot_no_truncate BEFORE TRUNCATE
    ON qtrade_data.collection_freshness_snapshot FOR EACH STATEMENT
    EXECUTE FUNCTION qtrade_data.reject_freshness_snapshot_change();
