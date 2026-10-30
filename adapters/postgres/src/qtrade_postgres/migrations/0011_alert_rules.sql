CREATE TABLE alert_rule_version (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    rule_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    source text NOT NULL CHECK (source IN ('market','risk','data','system')),
    condition text NOT NULL CHECK (length(btrim(condition)) BETWEEN 1 AND 512),
    severity text NOT NULL CHECK (severity IN ('info','warning','critical')),
    cooldown_seconds integer NOT NULL CHECK (cooldown_seconds BETWEEN 0 AND 86400),
    enabled boolean NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, rule_id, version)
);
CREATE INDEX alert_rule_version_current_idx
    ON alert_rule_version (workspace_id, rule_id, version DESC);
