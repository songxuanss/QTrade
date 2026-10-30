CREATE TABLE data_quality_issue (
    issue_id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id),
    dataset_id uuid NOT NULL,
    issue_code text NOT NULL CHECK(issue_code IN
        ('missing','duplicate','out_of_order','price_jump','volume_anomaly','calendar_deviation')),
    severity text NOT NULL CHECK(severity IN ('info','warning','critical')),
    instrument_id text NOT NULL,
    granularity text NOT NULL CHECK(granularity IN ('1m','5m','15m','1h','1d')),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL CHECK(ends_at>starts_at),
    batch_ids uuid[] NOT NULL DEFAULT '{}',
    evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object'),
    evidence_sha256 char(64) NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
    detected_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','claimed','closed')),
    claimed_by uuid REFERENCES qtrade_user(user_id),
    closed_by uuid REFERENCES qtrade_user(user_id),
    closed_at timestamptz,
    CHECK((status='open' AND claimed_by IS NULL AND closed_by IS NULL AND closed_at IS NULL)
       OR (status='claimed' AND claimed_by IS NOT NULL AND closed_by IS NULL AND closed_at IS NULL)
       OR (status='closed' AND claimed_by IS NOT NULL AND closed_by IS NOT NULL AND closed_at IS NOT NULL))
);
CREATE UNIQUE INDEX data_quality_identity ON data_quality_issue
    (workspace_id,dataset_id,issue_code,instrument_id,granularity,starts_at,evidence_sha256);
CREATE INDEX data_quality_browse ON data_quality_issue
    (workspace_id,dataset_id,status,starts_at DESC,issue_id);
CREATE FUNCTION guard_data_quality_issue() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'data quality evidence cannot be deleted'; END IF;
    IF NEW.issue_id<>OLD.issue_id OR NEW.workspace_id<>OLD.workspace_id
       OR NEW.dataset_id<>OLD.dataset_id OR NEW.issue_code<>OLD.issue_code
       OR NEW.severity<>OLD.severity OR NEW.instrument_id<>OLD.instrument_id
       OR NEW.granularity<>OLD.granularity OR NEW.starts_at<>OLD.starts_at
       OR NEW.ends_at<>OLD.ends_at OR NEW.batch_ids<>OLD.batch_ids
       OR NEW.evidence<>OLD.evidence OR NEW.evidence_sha256<>OLD.evidence_sha256
       OR NEW.detected_at<>OLD.detected_at THEN
        RAISE EXCEPTION 'data quality evidence is immutable';
    END IF;
    IF NOT ((OLD.status='open' AND NEW.status='claimed')
         OR (OLD.status='claimed' AND NEW.status='closed')) THEN
        RAISE EXCEPTION 'invalid data quality issue transition';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER data_quality_guard BEFORE UPDATE OR DELETE ON data_quality_issue
    FOR EACH ROW EXECUTE FUNCTION guard_data_quality_issue();
CREATE FUNCTION reject_data_quality_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'data quality evidence cannot be truncated'; END;
$$;
CREATE TRIGGER data_quality_no_truncate BEFORE TRUNCATE ON data_quality_issue
    FOR EACH STATEMENT EXECUTE FUNCTION reject_data_quality_truncate();
