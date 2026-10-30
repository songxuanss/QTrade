CREATE TABLE notification_delivery (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    notification_id uuid NOT NULL,
    channel text NOT NULL CHECK (channel IN ('in_app', 'email', 'webhook')),
    destination_id text NOT NULL CHECK (length(destination_id) BETWEEN 1 AND 320),
    subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 200),
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 8192),
    status text NOT NULL CHECK (status IN ('queued', 'running', 'delivered', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL,
    lease_token uuid,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, notification_id),
    CHECK ((status='running')=(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL))
);
CREATE INDEX notification_delivery_ready_idx
    ON notification_delivery(available_at, created_at)
    WHERE status IN ('queued', 'running');

CREATE TABLE notification_attempt (
    attempt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id uuid NOT NULL,
    notification_id uuid NOT NULL,
    attempt integer NOT NULL CHECK (attempt > 0),
    channel text NOT NULL CHECK (channel IN ('in_app', 'email', 'webhook')),
    target text NOT NULL CHECK (length(target) BETWEEN 1 AND 320),
    status text NOT NULL CHECK (status IN ('delivered', 'retrying', 'failed')),
    response_summary text NOT NULL CHECK (length(response_summary) <= 512),
    attempted_at timestamptz NOT NULL,
    UNIQUE (workspace_id, notification_id, attempt),
    FOREIGN KEY (workspace_id, notification_id)
        REFERENCES notification_delivery(workspace_id, notification_id) ON DELETE RESTRICT
);

CREATE TABLE in_app_notification (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    notification_id uuid NOT NULL,
    destination_id text NOT NULL CHECK (length(destination_id) BETWEEN 1 AND 320),
    subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 200),
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 8192),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, notification_id)
);
CREATE INDEX in_app_notification_inbox_idx
    ON in_app_notification(workspace_id, destination_id, created_at DESC);
