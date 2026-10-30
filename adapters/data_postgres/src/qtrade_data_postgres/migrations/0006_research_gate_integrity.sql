-- Tighten three-valued JSON checks without editing already-applied migration 0005.
CREATE OR REPLACE FUNCTION qtrade_data.check_research_publication_gate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE decision jsonb; artifact jsonb;
BEGIN
    SELECT result INTO decision FROM qtrade_data.research_quality
    WHERE scope_id=NEW.scope_id AND quality_id=NEW.quality_id AND artifact_id=NEW.artifact_id;
    SELECT manifest INTO artifact FROM qtrade_data.research_artifact
    WHERE scope_id=NEW.scope_id AND artifact_id=NEW.artifact_id;
    IF decision IS NULL OR artifact IS NULL
       OR COALESCE(decision->>'status','') NOT IN ('passed','warning')
       OR jsonb_typeof(decision->'issues') IS DISTINCT FROM 'array'
       OR jsonb_typeof(decision->'schema_version') IS DISTINCT FROM 'number'
       OR decision->>'schema_version' IS DISTINCT FROM '2'
       OR jsonb_typeof(artifact->'schema_version') IS DISTINCT FROM 'number'
       OR artifact->>'schema_version' IS DISTINCT FROM '2'
       OR jsonb_typeof(NEW.manifest->'schema_version') IS DISTINCT FROM 'number'
       OR NEW.manifest->>'schema_version' IS DISTINCT FROM '2'
       OR decision->>'scope_id' IS DISTINCT FROM NEW.scope_id::text
       OR decision->>'artifact_id' IS DISTINCT FROM NEW.artifact_id::text
       OR decision->>'quality_id' IS DISTINCT FROM NEW.quality_id::text
       OR artifact->>'scope_id' IS DISTINCT FROM NEW.scope_id::text
       OR artifact->>'artifact_id' IS DISTINCT FROM NEW.artifact_id::text
       OR NEW.manifest->>'scope_id' IS DISTINCT FROM NEW.scope_id::text
       OR NEW.manifest->>'publication_id' IS DISTINCT FROM NEW.publication_id::text
       OR COALESCE(artifact->>'records_sha256','') !~ '^[0-9a-f]{64}$'
       OR COALESCE(NEW.manifest->>'content_version','') !~ '^[0-9a-f]{64}$'
       OR decision->>'records_sha256' IS DISTINCT FROM artifact->>'records_sha256'
       OR NEW.manifest->'quality' IS DISTINCT FROM decision
       OR NEW.manifest->'artifact' IS DISTINCT FROM artifact THEN
        RAISE EXCEPTION 'research publication gate rejected';
    END IF;
    IF (decision->>'status'='passed' AND jsonb_array_length(decision->'issues')<>0)
       OR (decision->>'status'='warning' AND jsonb_array_length(decision->'issues')=0)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(decision->'issues') issue
                 WHERE COALESCE(issue->>'severity','')<>'warning') THEN
        RAISE EXCEPTION 'research publication quality issues rejected';
    END IF;
    RETURN NEW;
END;
$$;
