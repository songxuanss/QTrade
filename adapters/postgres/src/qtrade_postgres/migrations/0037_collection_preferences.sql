-- Workspace UI drafts are not executable collector plans and contain no credentials.
CREATE TABLE collection_preferences (
    user_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    draft jsonb NOT NULL CHECK (jsonb_typeof(draft)='object' AND draft ? 'schema_version'
        AND draft->'schema_version'='1'::jsonb),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id,workspace_id),
    FOREIGN KEY(workspace_id,user_id) REFERENCES membership(workspace_id,user_id)
);
