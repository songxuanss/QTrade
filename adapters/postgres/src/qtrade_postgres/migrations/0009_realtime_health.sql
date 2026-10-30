CREATE TABLE market_stream_health (
    workspace_id uuid NOT NULL REFERENCES workspace(workspace_id),
    source text NOT NULL CHECK(length(source) BETWEEN 1 AND 256),
    instrument_id text NOT NULL CHECK(length(instrument_id) BETWEEN 1 AND 256),
    status text NOT NULL CHECK(status IN ('healthy','degraded','disconnected','reconnecting')),
    updated_at timestamptz NOT NULL,
    heartbeat_at timestamptz,
    last_event_at timestamptz,
    last_sequence bigint CHECK(last_sequence IS NULL OR last_sequence>=0),
    latest_lag_ms integer CHECK(latest_lag_ms IS NULL OR latest_lag_ms>=0),
    max_lag_ms integer NOT NULL CHECK(max_lag_ms>=0),
    out_of_order_count bigint NOT NULL CHECK(out_of_order_count>=0),
    reconnect_count bigint NOT NULL CHECK(reconnect_count>=0),
    next_reconnect_at timestamptz,
    last_error_code text CHECK(last_error_code IS NULL OR last_error_code IN
        ('not_found','conflict','invalid_input','unsupported','unavailable','timeout',
         'rate_limited','unauthorized','checksum_mismatch','rejected')),
    PRIMARY KEY(workspace_id,source,instrument_id),
    CHECK((status IN ('disconnected','reconnecting'))=(next_reconnect_at IS NOT NULL)),
    CHECK(last_event_at IS NULL OR heartbeat_at IS NOT NULL)
);
CREATE INDEX market_stream_health_browse ON market_stream_health
    (workspace_id,status,updated_at DESC,source,instrument_id);
