-- Admission credits are charged once before an outbound attempt. Expiry releases
-- its ownership slot, never refunds an attempt whose external effect is unknown.
CREATE TABLE qtrade_data.usage_day (
    source text NOT NULL CHECK (source ~ '^[a-z][a-z0-9._-]{0,99}$'),
    day date NOT NULL,
    scope_key text NOT NULL CHECK (scope_key = 'source' OR scope_key ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
    credits bigint NOT NULL DEFAULT 0 CHECK (credits >= 0),
    requests bigint NOT NULL DEFAULT 0 CHECK (requests >= 0),
    PRIMARY KEY (source, day, scope_key)
);
CREATE TABLE qtrade_data.usage_request (
    request_id uuid PRIMARY KEY,
    source text NOT NULL CHECK (source ~ '^[a-z][a-z0-9._-]{0,99}$'),
    scope_id uuid,
    operation text NOT NULL CHECK (operation ~ '^[a-z][a-z0-9._-]{0,99}$'),
    credits integer NOT NULL CHECK (credits > 0 AND credits <= 1000000),
    day date NOT NULL,
    policy jsonb NOT NULL CHECK ((policy->>'version' = 'usage-policy.v1') IS TRUE),
    token uuid NOT NULL UNIQUE,
    reserved_at timestamptz NOT NULL,
    lease_until timestamptz NOT NULL CHECK (lease_until > reserved_at),
    state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','finished','expired')),
    finished_at timestamptz,
    outcome text CHECK (outcome IN ('success','failure','expired')),
    error_code text CHECK (error_code IN ('timeout','unavailable','unauthorized','rate_limited','unverified')),
    duration_ms bigint CHECK (duration_ms >= 0),
    response_bytes bigint CHECK (response_bytes >= 0),
    CHECK (finished_at IS NULL OR finished_at >= reserved_at),
    CHECK ((( state = 'reserved' AND finished_at IS NULL AND outcome IS NULL)
        OR (state = 'expired' AND finished_at IS NOT NULL AND outcome = 'expired')
        OR (state = 'finished' AND finished_at IS NOT NULL AND outcome IN ('success','failure'))) IS TRUE),
    CHECK (state <> 'finished' OR (duration_ms IS NOT NULL AND response_bytes IS NOT NULL)),
    CHECK (state <> 'finished' OR (outcome = 'success' AND error_code IS NULL)
        OR (outcome = 'failure' AND error_code IS NOT NULL))
);
CREATE INDEX usage_active_source ON qtrade_data.usage_request(source,lease_until)
    WHERE state = 'reserved';
CREATE INDEX usage_scoped_day ON qtrade_data.usage_request(source,scope_id,day,reserved_at);
