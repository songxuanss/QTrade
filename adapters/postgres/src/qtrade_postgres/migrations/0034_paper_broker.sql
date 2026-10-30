CREATE TABLE paper_account (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id) ON DELETE RESTRICT,
    account_id uuid NOT NULL,
    initial_cash numeric NOT NULL CHECK (initial_cash >= 0),
    cash numeric NOT NULL CHECK (cash >= 0),
    currency text NOT NULL CHECK (currency = 'CNY'),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id,account_id),
    CHECK (initial_cash <> 'NaN'::numeric AND cash <> 'NaN'::numeric)
);
CREATE FUNCTION validate_paper_account_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.workspace_id<>NEW.workspace_id OR OLD.account_id<>NEW.account_id
       OR OLD.initial_cash<>NEW.initial_cash OR OLD.currency<>NEW.currency
       OR OLD.created_at<>NEW.created_at THEN
        RAISE EXCEPTION 'Paper account identity is immutable' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER paper_account_validate_update BEFORE UPDATE ON paper_account
    FOR EACH ROW EXECUTE FUNCTION validate_paper_account_update();
CREATE TABLE paper_position (
    workspace_id uuid NOT NULL,
    account_id uuid NOT NULL,
    instrument_id text NOT NULL CHECK (length(instrument_id) BETWEEN 1 AND 256),
    quantity numeric NOT NULL CHECK (quantity > 0 AND quantity <> 'NaN'::numeric),
    average_price numeric NOT NULL CHECK (average_price > 0 AND average_price <> 'NaN'::numeric),
    PRIMARY KEY (workspace_id,account_id,instrument_id),
    FOREIGN KEY (workspace_id,account_id) REFERENCES paper_account(workspace_id,account_id)
        ON DELETE RESTRICT
);
CREATE TABLE paper_broker_receipt (
    workspace_id uuid NOT NULL,
    account_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    intent_id uuid NOT NULL,
    command jsonb NOT NULL CHECK (jsonb_typeof(command)='object'),
    receipt jsonb NOT NULL CHECK (jsonb_typeof(receipt)='object'),
    market_price numeric NOT NULL CHECK (market_price > 0 AND market_price <> 'NaN'::numeric),
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id,idempotency_key),
    UNIQUE (workspace_id,intent_id),
    FOREIGN KEY (workspace_id,account_id) REFERENCES paper_account(workspace_id,account_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (workspace_id,intent_id) REFERENCES order_execution(workspace_id,intent_id)
        ON DELETE RESTRICT,
    CHECK ((command->>'workspace_id')::uuid=workspace_id),
    CHECK ((command->>'idempotency_key')::uuid=idempotency_key),
    CHECK ((command->'intent'->>'intent_id')::uuid=intent_id),
    CHECK ((command->'intent'->>'account_id')::uuid=account_id),
    CHECK ((receipt->>'workspace_id')::uuid=workspace_id),
    CHECK ((receipt->>'account_id')::uuid=account_id)
);
CREATE FUNCTION reject_paper_receipt_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Paper broker receipts are immutable' USING ERRCODE='55000';
END $$;
CREATE TRIGGER paper_receipt_no_update_delete BEFORE UPDATE OR DELETE ON paper_broker_receipt
    FOR EACH ROW EXECUTE FUNCTION reject_paper_receipt_mutation();
CREATE TRIGGER paper_receipt_no_truncate BEFORE TRUNCATE ON paper_broker_receipt
    FOR EACH STATEMENT EXECUTE FUNCTION reject_paper_receipt_mutation();
