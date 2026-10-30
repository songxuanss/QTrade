-- Transport evidence is not a parsed page or publication. Keep it independent
-- so decoding/status failures remain traceable without changing frozen plans.
CREATE TABLE qtrade_data.research_response_receipt (
    scope_id uuid NOT NULL,
    request_id uuid NOT NULL,
    receipt_id uuid NOT NULL,
    acquisition_id uuid NOT NULL,
    response_index integer NOT NULL CHECK (response_index >= 0 AND response_index < 10),
    captured_at timestamptz NOT NULL,
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    PRIMARY KEY(scope_id,receipt_id),
    UNIQUE(scope_id,acquisition_id,response_index),
    FOREIGN KEY(scope_id,request_id) REFERENCES qtrade_data.research_run(scope_id,request_id),
    CHECK (payload->>'scope_id' IS NOT DISTINCT FROM scope_id::text),
    CHECK (payload->>'request_id' IS NOT DISTINCT FROM request_id::text),
    CHECK (payload->>'receipt_id' IS NOT DISTINCT FROM receipt_id::text),
    CHECK (payload->>'acquisition_id' IS NOT DISTINCT FROM acquisition_id::text),
    CHECK ((payload->>'response_index')::integer IS NOT DISTINCT FROM response_index),
    CHECK ((payload->>'captured_at')::timestamptz IS NOT DISTINCT FROM captured_at)
);
CREATE INDEX research_response_request_page ON qtrade_data.research_response_receipt
    (scope_id,request_id,captured_at DESC,receipt_id DESC);
CREATE TRIGGER research_response_immutable BEFORE UPDATE OR DELETE
ON qtrade_data.research_response_receipt FOR EACH ROW
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
CREATE TRIGGER research_response_no_truncate BEFORE TRUNCATE
ON qtrade_data.research_response_receipt FOR EACH STATEMENT
EXECUTE FUNCTION qtrade_data.reject_research_evidence_mutation();
