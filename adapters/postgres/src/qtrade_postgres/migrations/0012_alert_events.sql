CREATE TABLE alert_event (
    event_id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    source text NOT NULL CHECK (source IN ('market','risk','data','system')),
    source_event_id text NOT NULL CHECK (length(source_event_id) BETWEEN 1 AND 256),
    event_type text NOT NULL CHECK (length(event_type) BETWEEN 1 AND 128),
    severity text NOT NULL CHECK (severity IN ('info','warning','critical')),
    status text NOT NULL CHECK (status IN ('open','acknowledged','closed')),
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 160),
    message text NOT NULL CHECK (length(btrim(message)) BETWEEN 1 AND 4096),
    resource_id text CHECK (resource_id IS NULL OR length(resource_id) <= 256),
    occurred_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL CHECK (updated_at >= occurred_at),
    UNIQUE (workspace_id, source, source_event_id)
);
CREATE INDEX alert_event_workspace_time_idx ON alert_event (workspace_id, occurred_at DESC, event_id);
