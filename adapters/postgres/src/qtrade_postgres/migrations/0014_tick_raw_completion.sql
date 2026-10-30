ALTER TABLE ingestion_task DROP CONSTRAINT ingestion_task_check1;
ALTER TABLE ingestion_task ADD CONSTRAINT ingestion_task_completion_dataset_check
    CHECK ((status='completed' AND request->>'granularity'<>'tick')=(dataset_id IS NOT NULL));
