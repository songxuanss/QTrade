CREATE TABLE alert_evaluation_job (
    job_id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    actor_id uuid NOT NULL REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    source text NOT NULL CHECK (source IN ('market', 'risk', 'data', 'system')),
    facts jsonb NOT NULL CHECK (jsonb_typeof(facts)='object'),
    occurred_at timestamptz NOT NULL,
    resource_id text,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL,
    lease_token uuid,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CHECK ((status='running')=(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL))
);
CREATE INDEX alert_evaluation_job_ready_idx
    ON alert_evaluation_job(available_at, created_at)
    WHERE status IN ('queued', 'running');
