CREATE TABLE qtrade_data.research_quality(
    scope_id uuid NOT NULL, quality_id uuid NOT NULL, artifact_id uuid NOT NULL,
    result jsonb NOT NULL,
    PRIMARY KEY(scope_id,quality_id),
    FOREIGN KEY(scope_id,artifact_id) REFERENCES qtrade_data.research_artifact(scope_id,artifact_id),
    CHECK(result->>'scope_id'=scope_id::text),
    CHECK(result->>'quality_id'=quality_id::text),
    CHECK(result->>'artifact_id'=artifact_id::text),
    CHECK(result->>'status' IN ('passed','warning','blocked'))
);
CREATE TABLE qtrade_data.research_publication(
    scope_id uuid NOT NULL, publication_id uuid NOT NULL, artifact_id uuid NOT NULL,
    quality_id uuid NOT NULL, manifest jsonb NOT NULL,
    PRIMARY KEY(scope_id,publication_id),
    UNIQUE(scope_id,artifact_id,quality_id),
    FOREIGN KEY(scope_id,artifact_id) REFERENCES qtrade_data.research_artifact(scope_id,artifact_id),
    FOREIGN KEY(scope_id,quality_id) REFERENCES qtrade_data.research_quality(scope_id,quality_id),
    CHECK(manifest->>'scope_id'=scope_id::text),
    CHECK(manifest->>'publication_id'=publication_id::text)
);
CREATE TRIGGER research_quality_immutable BEFORE UPDATE OR DELETE
ON qtrade_data.research_quality FOR EACH ROW
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
CREATE TRIGGER research_publication_immutable BEFORE UPDATE OR DELETE
ON qtrade_data.research_publication FOR EACH ROW
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
CREATE FUNCTION qtrade_data.check_research_publication_gate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE decision jsonb; artifact jsonb;
BEGIN
    SELECT result INTO decision FROM qtrade_data.research_quality
    WHERE scope_id=NEW.scope_id AND quality_id=NEW.quality_id AND artifact_id=NEW.artifact_id;
    SELECT manifest INTO artifact FROM qtrade_data.research_artifact
    WHERE scope_id=NEW.scope_id AND artifact_id=NEW.artifact_id;
    IF decision IS NULL OR artifact IS NULL OR decision->>'status'='blocked'
       OR decision->>'records_sha256' IS DISTINCT FROM artifact->>'records_sha256'
       OR NEW.manifest->'quality' IS DISTINCT FROM decision
       OR NEW.manifest->'artifact' IS DISTINCT FROM artifact THEN
        RAISE EXCEPTION 'research publication gate rejected';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER research_publication_gate BEFORE INSERT
ON qtrade_data.research_publication FOR EACH ROW
EXECUTE FUNCTION qtrade_data.check_research_publication_gate();
