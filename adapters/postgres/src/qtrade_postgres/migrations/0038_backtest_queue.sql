ALTER TABLE backtest_run
    ADD COLUMN lease_owner uuid,
    ADD COLUMN lease_expires_at timestamptz,
    ADD COLUMN attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    ADD COLUMN cancel_requested boolean NOT NULL DEFAULT false;

CREATE INDEX backtest_run_queue_idx
    ON backtest_run(status,lease_expires_at,created_at)
    WHERE status IN ('queued','running');
