-- One immutable offline intent is committed with its ordinary queue entry.
CREATE TABLE qtrade_data.collection_replay (
    scope_id uuid NOT NULL,
    request_id uuid NOT NULL,
    part_id uuid NOT NULL,
    parent_job_id uuid NOT NULL,
    parent_part_id uuid NOT NULL,
    root_job_id uuid NOT NULL,
    root_part_id uuid NOT NULL,
    input_id uuid NOT NULL,
    control_sha256 text NOT NULL CHECK(control_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    PRIMARY KEY(scope_id,request_id),
    UNIQUE(scope_id,part_id),
    FOREIGN KEY(scope_id,request_id,part_id) REFERENCES qtrade_data.collection_job_part(scope_id,job_id,part_id),
    FOREIGN KEY(scope_id,parent_job_id,parent_part_id) REFERENCES qtrade_data.collection_job_part(scope_id,job_id,part_id),
    FOREIGN KEY(scope_id,root_job_id,root_part_id) REFERENCES qtrade_data.collection_job_part(scope_id,job_id,part_id),
    CHECK(request_id <> parent_job_id),
    CHECK((payload->>'schema_version') IS NOT DISTINCT FROM '2'),
    CHECK((payload->>'control_sha256') IS NOT DISTINCT FROM control_sha256),
    CHECK((payload->'command'->>'scope_id') IS NOT DISTINCT FROM scope_id::text),
    CHECK((payload->'command'->>'request_id') IS NOT DISTINCT FROM request_id::text),
    CHECK((payload->>'target_part_id') IS NOT DISTINCT FROM part_id::text),
    CHECK((payload->'command'->>'parent_job_id') IS NOT DISTINCT FROM parent_job_id::text),
    CHECK((payload->'command'->>'parent_part_id') IS NOT DISTINCT FROM parent_part_id::text),
    CHECK((payload->'input'->>'owner_job_id') IS NOT DISTINCT FROM root_job_id::text),
    CHECK((payload->'input'->>'part_id') IS NOT DISTINCT FROM root_part_id::text),
    CHECK((payload->'input'->>'input_id') IS NOT DISTINCT FROM input_id::text)
);
CREATE TRIGGER collection_replay_immutable BEFORE UPDATE OR DELETE ON qtrade_data.collection_replay
FOR EACH ROW EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
CREATE TRIGGER collection_replay_no_truncate BEFORE TRUNCATE ON qtrade_data.collection_replay
FOR EACH STATEMENT EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();

-- Once committed, even a missing handler/metadata must never turn an offline
-- queue row into a supplier download. Ordinary preexisting payloads are untouched.
CREATE FUNCTION qtrade_data.protect_collection_replay_marker() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.payload->>'execution_kind' = 'normalization_replay' AND NEW.payload IS DISTINCT FROM OLD.payload THEN
        RAISE EXCEPTION 'immutable offline execution payload';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER collection_replay_payload_guard BEFORE UPDATE OF payload ON qtrade_data.collection_part
FOR EACH ROW EXECUTE FUNCTION qtrade_data.protect_collection_replay_marker();
CREATE FUNCTION qtrade_data.require_collection_replay_marker() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM qtrade_data.collection_part p
        WHERE p.scope_id=NEW.scope_id AND p.part_id=NEW.part_id AND p.job_id=NEW.request_id
        AND p.payload->>'execution_kind'='normalization_replay') THEN
        RAISE EXCEPTION 'offline intent requires offline queue marker';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER collection_replay_marker_required BEFORE INSERT ON qtrade_data.collection_replay
FOR EACH ROW EXECUTE FUNCTION qtrade_data.require_collection_replay_marker();
