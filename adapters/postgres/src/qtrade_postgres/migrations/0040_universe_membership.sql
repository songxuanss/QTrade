-- Workspace-defined effective intervals for research universe members.
CREATE TABLE universe_membership (
    workspace_id uuid NOT NULL,
    membership_id uuid NOT NULL,
    instrument_id text NOT NULL,
    effective_from timestamptz NOT NULL,
    effective_to timestamptz NOT NULL,
    source text NOT NULL CHECK (source IN ('workspace.definition', 'supplier.membership')),
    evidence text NOT NULL,
    created_by uuid NOT NULL REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL,
    content_version text NOT NULL CHECK (content_version ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (workspace_id, membership_id),
    CHECK (effective_from < effective_to),
    CHECK ((payload->>'workspace_id') IS NOT DISTINCT FROM workspace_id::text),
    CHECK ((payload->>'membership_id') IS NOT DISTINCT FROM membership_id::text),
    CHECK ((payload->>'instrument_id') IS NOT DISTINCT FROM instrument_id)
);

CREATE INDEX universe_membership_instrument_idx
    ON universe_membership(workspace_id, instrument_id, effective_from, effective_to);
