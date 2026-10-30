-- Publication metadata is the visibility boundary for separate immutable market snapshots.
CREATE TABLE market_dataset (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id),
    dataset_id uuid NOT NULL,
    parent_dataset_id uuid,
    version integer NOT NULL CHECK(version>=1),
    request_sha256 char(64) NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
    quality_report_sha256 char(64) NOT NULL CHECK(quality_report_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
    published_by uuid NOT NULL REFERENCES qtrade_user(user_id),
    published_at timestamptz NOT NULL DEFAULT now(),
    creation_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
    PRIMARY KEY(workspace_id,dataset_id),
    FOREIGN KEY(workspace_id,parent_dataset_id) REFERENCES market_dataset(workspace_id,dataset_id),
    CHECK(parent_dataset_id IS NULL OR parent_dataset_id<>dataset_id),
    CHECK((payload->>'workspace_id') IS NOT DISTINCT FROM workspace_id::text),
    CHECK((payload->>'dataset_id') IS NOT DISTINCT FROM dataset_id::text),
    CHECK((payload->>'version') IS NOT DISTINCT FROM version::text)
);
CREATE TABLE market_dataset_batch (
    workspace_id uuid NOT NULL,
    dataset_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK(ordinal>=0),
    PRIMARY KEY(workspace_id,dataset_id,batch_id),
    UNIQUE(workspace_id,dataset_id,ordinal),
    FOREIGN KEY(workspace_id,dataset_id) REFERENCES market_dataset(workspace_id,dataset_id),
    FOREIGN KEY(workspace_id,batch_id) REFERENCES raw_data_batch(workspace_id,batch_id)
);
CREATE INDEX market_dataset_lineage ON market_dataset(workspace_id,parent_dataset_id);
CREATE FUNCTION validate_market_dataset_lineage() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE parent market_dataset%ROWTYPE;
BEGIN
    IF NEW.parent_dataset_id IS NULL THEN
        IF NEW.version<>1 THEN RAISE EXCEPTION 'invalid initial dataset version'; END IF;
    ELSE
        SELECT * INTO parent FROM market_dataset
            WHERE workspace_id=NEW.workspace_id AND dataset_id=NEW.parent_dataset_id;
        IF NOT FOUND OR NEW.version<>parent.version+1
            OR (NEW.payload->'batch_ids') IS DISTINCT FROM (parent.payload->'batch_ids') THEN
            RAISE EXCEPTION 'invalid dataset lineage';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER market_dataset_validate_lineage BEFORE INSERT ON market_dataset
    FOR EACH ROW EXECUTE FUNCTION validate_market_dataset_lineage();
CREATE FUNCTION validate_market_dataset_batch() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE publication market_dataset%ROWTYPE;
BEGIN
    SELECT * INTO publication FROM market_dataset
        WHERE workspace_id=NEW.workspace_id AND dataset_id=NEW.dataset_id;
    IF NOT FOUND OR publication.creation_xid<>pg_current_xact_id()
        OR (publication.payload->'batch_ids'->>NEW.ordinal) IS DISTINCT FROM NEW.batch_id::text THEN
        RAISE EXCEPTION 'dataset provenance must be published atomically';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER market_dataset_batch_validate BEFORE INSERT ON market_dataset_batch
    FOR EACH ROW EXECUTE FUNCTION validate_market_dataset_batch();
CREATE FUNCTION validate_market_dataset_complete() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
DECLARE source_count bigint;
BEGIN
    SELECT count(*) INTO source_count FROM market_dataset_batch
        WHERE workspace_id=NEW.workspace_id AND dataset_id=NEW.dataset_id;
    IF source_count<1
        OR source_count IS DISTINCT FROM jsonb_array_length(NEW.payload->'batch_ids')::bigint THEN
        RAISE EXCEPTION 'dataset provenance is incomplete';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER market_dataset_complete AFTER INSERT ON market_dataset
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_market_dataset_complete();
CREATE FUNCTION reject_market_dataset_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'dataset metadata is immutable';
END;
$$;
CREATE TRIGGER market_dataset_no_change BEFORE UPDATE OR DELETE ON market_dataset
    FOR EACH ROW EXECUTE FUNCTION reject_market_dataset_change();
CREATE TRIGGER market_dataset_no_truncate BEFORE TRUNCATE ON market_dataset
    FOR EACH STATEMENT EXECUTE FUNCTION reject_market_dataset_change();
CREATE TRIGGER market_dataset_batch_no_change BEFORE UPDATE OR DELETE ON market_dataset_batch
    FOR EACH ROW EXECUTE FUNCTION reject_market_dataset_change();
CREATE TRIGGER market_dataset_batch_no_truncate BEFORE TRUNCATE ON market_dataset_batch
    FOR EACH STATEMENT EXECUTE FUNCTION reject_market_dataset_change();
