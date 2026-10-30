CREATE INDEX data_quality_workspace_browse ON data_quality_issue
    (workspace_id,detected_at DESC,issue_id);
