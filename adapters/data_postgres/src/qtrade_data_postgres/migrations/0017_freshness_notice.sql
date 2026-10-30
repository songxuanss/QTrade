-- An outbox fact and its verified snapshot commit together. Delivery progress
-- belongs in a separate table; never mutate the original incident evidence.
CREATE TABLE qtrade_data.collection_freshness_notice (
    sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    scope_id uuid NOT NULL,
    notice_id uuid NOT NULL,
    key_sha256 text NOT NULL CHECK(key_sha256 ~ '^[a-f0-9]{64}$'),
    episode_id uuid NOT NULL,
    kind text NOT NULL CHECK(kind IN ('opened','recovered')),
    occurred_at timestamptz NOT NULL,
    snapshot_id uuid NOT NULL,
    notice_sha256 text NOT NULL CHECK(notice_sha256 ~ '^[a-f0-9]{64}$'),
    payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
    stored_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,notice_id),
    UNIQUE(scope_id,key_sha256,episode_id,kind),
    FOREIGN KEY(scope_id,snapshot_id)
        REFERENCES qtrade_data.collection_freshness_snapshot(scope_id,snapshot_id),
    CHECK((payload->'schema_version'='1'::jsonb
        AND (payload->>'actor_id')::uuid IS NOT NULL
        AND payload->>'scope_id'=scope_id::text
        AND payload->>'notice_id'=notice_id::text
        AND payload->>'episode_id'=episode_id::text
        AND payload->>'kind'=kind
        AND (payload->>'occurred_at')::timestamptz=occurred_at
        AND payload->'result'->>'scope_id'=scope_id::text
        AND payload->'result'->'key'->>'scope_id'=scope_id::text
        AND payload->'result'->'evidence'->'key'=payload->'result'->'key'
        AND (payload->'result'->>'checked_at')::timestamptz=occurred_at) IS TRUE)
);
CREATE INDEX collection_freshness_notice_head ON qtrade_data.collection_freshness_notice
    (scope_id,key_sha256,sequence DESC);
CREATE FUNCTION qtrade_data.reject_freshness_notice_change() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'freshness notices are immutable';
END;
$$;
CREATE TRIGGER freshness_notice_no_change BEFORE UPDATE OR DELETE
    ON qtrade_data.collection_freshness_notice FOR EACH ROW
    EXECUTE FUNCTION qtrade_data.reject_freshness_notice_change();
CREATE TRIGGER freshness_notice_no_truncate BEFORE TRUNCATE
    ON qtrade_data.collection_freshness_notice FOR EACH STATEMENT
    EXECUTE FUNCTION qtrade_data.reject_freshness_notice_change();
