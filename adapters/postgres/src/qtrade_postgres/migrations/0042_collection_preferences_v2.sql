-- Draft schema v2 adds ordered multi-dimension cards. Existing v1 drafts stay
-- valid, and the guard still rejects null or unknown versions.
ALTER TABLE collection_preferences
    DROP CONSTRAINT collection_preferences_draft_check;
ALTER TABLE collection_preferences
    ADD CONSTRAINT collection_preferences_draft_check
    CHECK (
        jsonb_typeof(draft) = 'object'
        AND draft ? 'schema_version'
        AND draft->'schema_version' IN ('1'::jsonb, '2'::jsonb)
    );
