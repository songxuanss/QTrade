CREATE TABLE kill_switch_state (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    scope_id uuid NOT NULL,
    account_id uuid,
    active boolean NOT NULL,
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
    changed_by uuid,
    changed_at timestamptz NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (workspace_id,scope_id),
    CHECK ((scope_id='00000000-0000-0000-0000-000000000000'::uuid)=(account_id IS NULL)),
    CHECK ((changed_by IS NULL) OR changed_by<>'00000000-0000-0000-0000-000000000000'::uuid),
    FOREIGN KEY (changed_by) REFERENCES qtrade_user(user_id) ON DELETE RESTRICT
);

CREATE TABLE kill_switch_recovery_request (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    request_id uuid NOT NULL,
    account_id uuid,
    requested_by uuid NOT NULL REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    requested_at timestamptz NOT NULL,
    approved_by uuid REFERENCES qtrade_user(user_id) ON DELETE RESTRICT,
    approved_at timestamptz,
    PRIMARY KEY (workspace_id,request_id),
    CHECK ((approved_by IS NULL)=(approved_at IS NULL)),
    CHECK (approved_by IS NULL OR approved_by<>requested_by)
);
CREATE INDEX kill_switch_recovery_pending_idx
    ON kill_switch_recovery_request(workspace_id,requested_at,request_id)
    WHERE approved_by IS NULL;
