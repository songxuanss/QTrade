CREATE TABLE qtrade_data.universe_snapshot (
    scope_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    universe_id text NOT NULL CHECK(length(universe_id) BETWEEN 1 AND 256),
    version integer NOT NULL CHECK(version > 0),
    observed_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
    PRIMARY KEY(scope_id,snapshot_id),
    UNIQUE(scope_id,universe_id,version),
    CHECK((payload->>'scope_id') IS NOT DISTINCT FROM scope_id::text),
    CHECK((payload->>'snapshot_id') IS NOT DISTINCT FROM snapshot_id::text),
    CHECK((payload->>'version') IS NOT DISTINCT FROM version::text),
    CHECK((payload->>'universe_id') IS NOT DISTINCT FROM universe_id),
    CHECK((payload->>'content_sha256') IS NOT DISTINCT FROM content_sha256)
);
CREATE INDEX universe_snapshot_page
    ON qtrade_data.universe_snapshot(scope_id,observed_at DESC,snapshot_id DESC);
CREATE FUNCTION qtrade_data.immutable_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'reference snapshot is immutable'; END;
$$;
CREATE TRIGGER universe_snapshot_immutable
BEFORE UPDATE OR DELETE OR TRUNCATE ON qtrade_data.universe_snapshot
FOR EACH STATEMENT EXECUTE FUNCTION qtrade_data.immutable_snapshot();
ALTER TABLE qtrade_data.universe_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE qtrade_data.universe_snapshot FORCE ROW LEVEL SECURITY;
CREATE POLICY universe_scope ON qtrade_data.universe_snapshot
    USING(scope_id = NULLIF(current_setting('qtrade_data.scope_id',true),'')::uuid)
    WITH CHECK(scope_id = NULLIF(current_setting('qtrade_data.scope_id',true),'')::uuid);
