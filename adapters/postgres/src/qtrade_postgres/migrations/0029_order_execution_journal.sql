CREATE TABLE order_execution (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    intent_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    command jsonb NOT NULL CHECK (jsonb_typeof(command)='object'),
    order_payload jsonb NOT NULL CHECK (jsonb_typeof(order_payload)='object'),
    events jsonb NOT NULL CHECK (jsonb_typeof(events)='array' AND jsonb_array_length(events)>=3),
    broker_order jsonb CHECK (broker_order IS NULL OR jsonb_typeof(broker_order)='object'),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id,intent_id),
    UNIQUE (workspace_id,idempotency_key),
    CHECK ((command->>'workspace_id')::uuid=workspace_id),
    CHECK ((command->'intent'->>'intent_id')::uuid=intent_id),
    CHECK ((command->>'idempotency_key')::uuid=idempotency_key),
    CHECK ((order_payload->>'workspace_id')::uuid=workspace_id),
    CHECK ((order_payload->>'intent_id')::uuid=intent_id),
    CHECK ((jsonb_array_length(events))=((order_payload->>'revision')::int))
);
CREATE INDEX order_execution_workspace_updated_idx
    ON order_execution(workspace_id,updated_at DESC,intent_id);
CREATE FUNCTION validate_order_execution_update() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE i integer;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'order execution journal is append-only' USING ERRCODE='55000';
    END IF;
    IF OLD.command<>NEW.command OR OLD.workspace_id<>NEW.workspace_id
       OR OLD.intent_id<>NEW.intent_id OR OLD.idempotency_key<>NEW.idempotency_key THEN
        RAISE EXCEPTION 'order command identity is immutable' USING ERRCODE='55000';
    END IF;
    IF jsonb_array_length(NEW.events)<jsonb_array_length(OLD.events)
       OR jsonb_array_length(NEW.events)>jsonb_array_length(OLD.events)+1 THEN
        RAISE EXCEPTION 'order event history cannot regress or skip revisions' USING ERRCODE='55000';
    END IF;
    FOR i IN 0..jsonb_array_length(OLD.events)-1 LOOP
        IF OLD.events->i IS DISTINCT FROM NEW.events->i THEN
            RAISE EXCEPTION 'order event history is append-only' USING ERRCODE='55000';
        END IF;
    END LOOP;
    IF OLD.order_payload->>'status' IN ('filled','cancelled','rejected','failed')
       AND NEW.order_payload IS DISTINCT FROM OLD.order_payload THEN
        RAISE EXCEPTION 'terminal order is immutable' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER order_execution_validate_update BEFORE UPDATE OR DELETE ON order_execution
    FOR EACH ROW EXECUTE FUNCTION validate_order_execution_update();
CREATE FUNCTION reject_order_execution_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'order execution journal is append-only' USING ERRCODE='55000';
END $$;
CREATE TRIGGER order_execution_no_truncate BEFORE TRUNCATE ON order_execution
    FOR EACH STATEMENT EXECUTE FUNCTION reject_order_execution_truncate();
