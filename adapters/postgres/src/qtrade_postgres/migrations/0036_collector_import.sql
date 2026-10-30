CREATE TABLE collector_import(
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id),
    publication_id uuid NOT NULL,
    state text NOT NULL CHECK(state IN ('waiting','failed','visible')),
    error_code text, attempts integer NOT NULL DEFAULT 0,
    manifest jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(workspace_id,publication_id)
);
CREATE INDEX collector_import_name ON collector_import(workspace_id,(manifest->>'instrument_id'),updated_at DESC);
