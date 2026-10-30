-- Offline execution intent and old RAW lineage are immutable; job state remains
-- in the existing leased queue. Never mutate the parent's run or publication.
CREATE TABLE qtrade_data.research_replay (
    scope_id uuid NOT NULL,
    request_id uuid NOT NULL,
    parent_request_id uuid NOT NULL,
    control_sha256 text NOT NULL CHECK (control_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    PRIMARY KEY(scope_id,request_id),
    FOREIGN KEY(scope_id,request_id) REFERENCES qtrade_data.research_run(scope_id,request_id),
    FOREIGN KEY(scope_id,parent_request_id) REFERENCES qtrade_data.research_run(scope_id,request_id),
    CHECK(request_id <> parent_request_id),
    CHECK(payload->'command'->>'scope_id' IS NOT DISTINCT FROM scope_id::text),
    CHECK(payload->'command'->>'request_id' IS NOT DISTINCT FROM request_id::text),
    CHECK(payload->'command'->>'parent_request_id' IS NOT DISTINCT FROM parent_request_id::text),
    CHECK(payload->>'control_sha256' IS NOT DISTINCT FROM control_sha256)
);
CREATE TABLE qtrade_data.research_replay_input (
    scope_id uuid NOT NULL,
    request_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK(ordinal >= 0 AND ordinal < 1000),
    PRIMARY KEY(scope_id,request_id,batch_id),
    UNIQUE(scope_id,request_id,ordinal),
    FOREIGN KEY(scope_id,request_id) REFERENCES qtrade_data.research_replay(scope_id,request_id),
    FOREIGN KEY(scope_id,batch_id) REFERENCES qtrade_data.research_raw_batch(scope_id,batch_id)
);
CREATE TRIGGER research_replay_immutable BEFORE UPDATE OR DELETE
ON qtrade_data.research_replay FOR EACH ROW
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
CREATE TRIGGER research_replay_no_truncate BEFORE TRUNCATE
ON qtrade_data.research_replay FOR EACH STATEMENT
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
CREATE TRIGGER research_replay_input_immutable BEFORE UPDATE OR DELETE
ON qtrade_data.research_replay_input FOR EACH ROW
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
CREATE TRIGGER research_replay_input_no_truncate BEFORE TRUNCATE
ON qtrade_data.research_replay_input FOR EACH STATEMENT
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
