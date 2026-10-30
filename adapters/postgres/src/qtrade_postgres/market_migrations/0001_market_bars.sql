CREATE EXTENSION IF NOT EXISTS timescaledb WITH SCHEMA public;
CREATE TABLE market_dataset_snapshot (
    workspace_id uuid NOT NULL,
    dataset_id uuid NOT NULL,
    content_sha256 char(64) NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
    record_count bigint NOT NULL CHECK(record_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    creation_xid bigint NOT NULL DEFAULT txid_current(),
    PRIMARY KEY(workspace_id,dataset_id)
);
CREATE TABLE market_bar (
    workspace_id uuid NOT NULL,
    dataset_id uuid NOT NULL,
    instrument_id text NOT NULL,
    granularity text NOT NULL CHECK(granularity IN ('1m','5m','15m','1h','1d')),
    timestamp timestamptz NOT NULL,
    payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
    PRIMARY KEY(workspace_id,dataset_id,instrument_id,granularity,timestamp)
);
SELECT public.create_hypertable('market_bar','timestamp',if_not_exists=>TRUE);
CREATE FUNCTION reject_market_data_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'market datasets are immutable';
END;
$$;
CREATE TRIGGER market_dataset_no_change BEFORE UPDATE OR DELETE ON market_dataset_snapshot
    FOR EACH ROW EXECUTE FUNCTION reject_market_data_change();
CREATE TRIGGER market_dataset_no_truncate BEFORE TRUNCATE ON market_dataset_snapshot
    FOR EACH STATEMENT EXECUTE FUNCTION reject_market_data_change();
CREATE TRIGGER market_bar_no_change BEFORE UPDATE OR DELETE ON market_bar
    FOR EACH ROW EXECUTE FUNCTION reject_market_data_change();
CREATE TRIGGER market_bar_no_truncate BEFORE TRUNCATE ON market_bar
    FOR EACH STATEMENT EXECUTE FUNCTION reject_market_data_change();

-- A hypertable FK adds DDL locks during first-chunk creation, which can deadlock
-- concurrent snapshot creators. Immutable parents plus this insert check enforce
-- existence and prevent later appends without that FK lock cycle.
CREATE FUNCTION require_market_snapshot_transaction() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM market_dataset_snapshot
        WHERE workspace_id=NEW.workspace_id AND dataset_id=NEW.dataset_id
            AND creation_xid=txid_current()
    ) THEN
        RAISE EXCEPTION 'bars require their snapshot creation transaction' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER market_bar_insert_scope BEFORE INSERT ON market_bar
    FOR EACH ROW EXECUTE FUNCTION require_market_snapshot_transaction();
