-- Durable replay protection: reserve and revoke in the same transaction.
CREATE TABLE logout_receipt (
    issuer text NOT NULL CHECK (issuer <> ''),
    jti text NOT NULL CHECK (length(jti) BETWEEN 1 AND 256),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz NOT NULL,
    PRIMARY KEY (issuer, jti),
    CHECK (expires_at > consumed_at)
);
CREATE INDEX logout_receipt_expiry ON logout_receipt(expires_at);
