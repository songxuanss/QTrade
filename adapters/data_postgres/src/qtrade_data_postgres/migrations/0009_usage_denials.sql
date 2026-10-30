-- Rejected attempts have no outbound effect and consume no credits. They are
-- stored separately after the failed admission transaction has rolled back.
CREATE TABLE qtrade_data.usage_denial (
    request_id uuid PRIMARY KEY,
    source text NOT NULL CHECK (source ~ '^[a-z][a-z0-9._-]{0,99}$'),
    scope_id uuid,
    operation text NOT NULL CHECK (operation ~ '^[a-z][a-z0-9._-]{0,99}$'),
    credits integer NOT NULL CHECK (credits > 0 AND credits <= 1000000),
    day date NOT NULL,
    denied_at timestamptz NOT NULL,
    reason text NOT NULL CHECK (reason IN ('source_daily','scope_daily','source_concurrency','scope_concurrency')),
    policy jsonb NOT NULL CHECK ((policy->>'version' = 'usage-policy.v1') IS TRUE),
    CHECK (scope_id IS NOT NULL OR operation LIKE 'metadata.%')
);
CREATE INDEX usage_denial_scope_day ON qtrade_data.usage_denial(source,scope_id,day,denied_at DESC);
