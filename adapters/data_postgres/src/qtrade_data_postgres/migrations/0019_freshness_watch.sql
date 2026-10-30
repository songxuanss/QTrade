-- Persist the authorization subject and due slot, never credentials. A revision
-- change fences an in-flight check before it can commit operational evidence.
CREATE TABLE qtrade_data.collection_freshness_watch (
    scope_id uuid NOT NULL,
    watch_id uuid NOT NULL,
    key_sha256 text NOT NULL CHECK(key_sha256 ~ '^[a-f0-9]{64}$'),
    revision bigint NOT NULL CHECK(revision>0),
    enabled boolean NOT NULL DEFAULT false,
    next_due_at timestamptz NOT NULL,
    lease_token uuid,
    lease_expires_at timestamptz,
    payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
    PRIMARY KEY(scope_id,watch_id),
    UNIQUE(scope_id,key_sha256),
    CHECK((lease_token IS NULL)=(lease_expires_at IS NULL)),
    CHECK(enabled OR lease_token IS NULL),
    CHECK((payload->'schema_version'='1'::jsonb
        AND payload->>'scope_id'=scope_id::text
        AND payload->>'watch_id'=watch_id::text
        AND payload->'key'->>'scope_id'=scope_id::text
        AND (payload->>'revision')::bigint=revision
        AND (payload->>'enabled')::boolean=enabled
        AND (payload->>'next_due_at')::timestamptz=next_due_at) IS TRUE)
);
CREATE INDEX collection_freshness_watch_due ON qtrade_data.collection_freshness_watch
    (next_due_at,scope_id,watch_id) WHERE enabled;
CREATE TABLE qtrade_data.collection_freshness_watch_revision (
    scope_id uuid NOT NULL,
    watch_id uuid NOT NULL,
    revision bigint NOT NULL,
    actor_id uuid NOT NULL,
    action text NOT NULL CHECK(action IN ('created','enabled','disabled','paused')),
    payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[a-f0-9]{64}$'),
    payload jsonb NOT NULL,
    stored_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,watch_id,revision),
    FOREIGN KEY(scope_id,watch_id)
        REFERENCES qtrade_data.collection_freshness_watch(scope_id,watch_id),
    CHECK((payload->>'scope_id'=scope_id::text
        AND payload->>'watch_id'=watch_id::text
        AND (payload->>'revision')::bigint=revision) IS TRUE)
);
CREATE TRIGGER freshness_watch_revision_no_change BEFORE UPDATE OR DELETE
    ON qtrade_data.collection_freshness_watch_revision FOR EACH ROW
    EXECUTE FUNCTION qtrade_data.reject_freshness_notice_change();
CREATE TRIGGER freshness_watch_revision_no_truncate BEFORE TRUNCATE
    ON qtrade_data.collection_freshness_watch_revision FOR EACH STATEMENT
    EXECUTE FUNCTION qtrade_data.reject_freshness_notice_change();
CREATE INDEX collection_freshness_candidate ON qtrade_data.collection_part
    (scope_id,(publication->>'source'),(publication->>'instrument_id'),
    (publication->>'mapping_version'),updated_at DESC,part_id DESC)
    WHERE state='completed' AND publication->>'dataset_kind'='bars'
    AND publication->>'granularity'='1d'
    AND publication->'evidence'->>'adjustment'='none';
