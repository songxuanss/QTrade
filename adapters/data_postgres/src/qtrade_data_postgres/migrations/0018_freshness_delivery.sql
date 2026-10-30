-- Delivery ownership is mutable; the event evidence in 0017 remains immutable.
CREATE TABLE qtrade_data.collection_freshness_delivery (
    scope_id uuid NOT NULL,
    notice_id uuid NOT NULL,
    consumer_id text NOT NULL CHECK(consumer_id ~ '^[a-zA-Z0-9_-]{1,64}$'),
    state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','leased','delivered')),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_expires_at timestamptz,
    attempts bigint NOT NULL DEFAULT 0 CHECK(attempts>=0),
    delivered_at timestamptz,
    PRIMARY KEY(scope_id,notice_id,consumer_id),
    FOREIGN KEY(scope_id,notice_id)
        REFERENCES qtrade_data.collection_freshness_notice(scope_id,notice_id),
    CHECK((state='pending' AND lease_token IS NULL AND lease_expires_at IS NULL
            AND delivered_at IS NULL)
        OR (state='leased' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL
            AND delivered_at IS NULL AND attempts>0)
        OR (state='delivered' AND lease_token IS NOT NULL AND lease_expires_at IS NULL
            AND delivered_at IS NOT NULL AND attempts>0))
);
CREATE INDEX collection_freshness_delivery_due ON qtrade_data.collection_freshness_delivery
    (consumer_id,available_at) WHERE state<>'delivered';
