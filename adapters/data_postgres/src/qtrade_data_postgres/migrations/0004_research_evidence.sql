-- Additive evidence storage. Archive existence is not a publication or quality pass.
CREATE TABLE qtrade_data.research_run(
    scope_id uuid NOT NULL, request_id uuid NOT NULL, actor_id uuid NOT NULL,
    plan_sha256 text NOT NULL CHECK(plan_sha256 ~ '^[0-9a-f]{64}$'),
    plan jsonb NOT NULL,
    stage text NOT NULL DEFAULT 'registered'
        CHECK(stage IN ('registered','raw_persisted','standard_persisted')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,request_id),
    CHECK(plan->'request'->>'scope_id'=scope_id::text),
    CHECK(plan->'request'->>'request_id'=request_id::text),
    CHECK(plan->>'plan_sha256'=plan_sha256)
);
CREATE TABLE qtrade_data.research_raw_batch(
    scope_id uuid NOT NULL, batch_id uuid NOT NULL, request_id uuid NOT NULL,
    manifest jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,batch_id),
    FOREIGN KEY(scope_id,request_id) REFERENCES qtrade_data.research_run(scope_id,request_id),
    CHECK(manifest->>'scope_id'=scope_id::text),
    CHECK(manifest->>'request_id'=request_id::text),
    CHECK(manifest->>'batch_id'=batch_id::text)
);
CREATE TABLE qtrade_data.research_artifact(
    scope_id uuid NOT NULL, artifact_id uuid NOT NULL, request_id uuid NOT NULL,
    manifest jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,artifact_id),
    FOREIGN KEY(scope_id,request_id) REFERENCES qtrade_data.research_run(scope_id,request_id),
    CHECK(manifest->>'scope_id'=scope_id::text),
    CHECK(manifest->>'request_id'=request_id::text),
    CHECK(manifest->>'artifact_id'=artifact_id::text)
);
CREATE TABLE qtrade_data.research_artifact_raw(
    scope_id uuid NOT NULL, artifact_id uuid NOT NULL, batch_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK(ordinal>=0),
    PRIMARY KEY(scope_id,artifact_id,batch_id), UNIQUE(scope_id,artifact_id,ordinal),
    FOREIGN KEY(scope_id,artifact_id) REFERENCES qtrade_data.research_artifact(scope_id,artifact_id),
    FOREIGN KEY(scope_id,batch_id) REFERENCES qtrade_data.research_raw_batch(scope_id,batch_id)
);
CREATE INDEX research_raw_run ON qtrade_data.research_raw_batch(scope_id,request_id);
CREATE INDEX research_artifact_run ON qtrade_data.research_artifact(scope_id,request_id);

CREATE FUNCTION qtrade_data.reject_research_evidence_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'research evidence is append only';
END;
$$;
CREATE TRIGGER research_raw_immutable BEFORE UPDATE OR DELETE
ON qtrade_data.research_raw_batch FOR EACH ROW
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
CREATE TRIGGER research_artifact_immutable BEFORE UPDATE OR DELETE
ON qtrade_data.research_artifact FOR EACH ROW
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
CREATE TRIGGER research_lineage_immutable BEFORE UPDATE OR DELETE
ON qtrade_data.research_artifact_raw FOR EACH ROW
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
CREATE FUNCTION qtrade_data.protect_research_plan() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.scope_id IS DISTINCT FROM OLD.scope_id
       OR NEW.request_id IS DISTINCT FROM OLD.request_id
       OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
       OR NEW.plan_sha256 IS DISTINCT FROM OLD.plan_sha256
       OR NEW.plan IS DISTINCT FROM OLD.plan THEN
        RAISE EXCEPTION 'research plan is immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER research_plan_immutable BEFORE UPDATE
ON qtrade_data.research_run FOR EACH ROW EXECUTE FUNCTION qtrade_data.protect_research_plan();
