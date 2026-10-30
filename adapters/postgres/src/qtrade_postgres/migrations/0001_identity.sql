-- Expand-only identity foundation. IDs are supplied by application use cases.
CREATE TABLE qtrade_user (
    user_id uuid PRIMARY KEY,
    issuer text NOT NULL CHECK (issuer <> ''),
    subject text NOT NULL CHECK (subject <> ''),
    display_name text NOT NULL,
    email text,
    disabled boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

CREATE TABLE workspace (
    workspace_id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 256),
    kind text NOT NULL CHECK (kind IN ('personal', 'organization')),
    owner_id uuid REFERENCES qtrade_user(user_id),
    live_trading_enabled boolean NOT NULL DEFAULT false,
    archived boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (kind <> 'personal' OR owner_id IS NOT NULL)
);
CREATE UNIQUE INDEX workspace_personal_owner ON workspace(owner_id) WHERE kind = 'personal';

CREATE TABLE membership (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id),
    user_id uuid NOT NULL REFERENCES qtrade_user(user_id),
    role text NOT NULL CHECK (role IN ('viewer', 'analyst', 'trader', 'risk_manager', 'admin')),
    active boolean NOT NULL DEFAULT true,
    created_by uuid REFERENCES qtrade_user(user_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX membership_user ON membership(user_id) WHERE active;

CREATE TABLE session (
    session_digest char(64) PRIMARY KEY CHECK (session_digest ~ '^[0-9a-f]{64}$'),
    user_id uuid NOT NULL REFERENCES qtrade_user(user_id),
    current_workspace_id uuid,
    sso_sid text,
    csrf_digest char(64) NOT NULL CHECK (csrf_digest ~ '^[0-9a-f]{64}$'),
    token_ciphertext bytea,
    encryption_key_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    FOREIGN KEY (current_workspace_id, user_id) REFERENCES membership(workspace_id, user_id),
    CHECK (expires_at > created_at),
    CHECK ((token_ciphertext IS NULL) = (encryption_key_id IS NULL)),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX session_user_active ON session(user_id) WHERE revoked_at IS NULL;
CREATE INDEX session_expiry ON session(expires_at);
CREATE INDEX session_sso_sid ON session(sso_sid) WHERE revoked_at IS NULL;

CREATE TABLE audit_event (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id uuid REFERENCES workspace(workspace_id),
    actor_type text NOT NULL CHECK (actor_type IN ('user', 'system', 'anonymous')),
    actor_id uuid REFERENCES qtrade_user(user_id),
    action text NOT NULL CHECK (length(action) BETWEEN 1 AND 256),
    result text NOT NULL CHECK (result IN ('success', 'denied', 'failure')),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL CHECK (length(source) BETWEEN 1 AND 256),
    source_ip inet,
    trace_id uuid NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    CHECK ((actor_type = 'user') = (actor_id IS NOT NULL))
);
CREATE INDEX audit_workspace_time ON audit_event(workspace_id, occurred_at DESC, event_id DESC);
CREATE INDEX audit_actor_time ON audit_event(actor_id, occurred_at DESC);

CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit events are append only' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER audit_no_update_delete BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation();
