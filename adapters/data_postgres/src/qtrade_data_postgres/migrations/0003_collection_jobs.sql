CREATE TABLE qtrade_data.collection_job(
    scope_id uuid NOT NULL, job_id uuid NOT NULL, actor_id uuid NOT NULL,
    request jsonb NOT NULL, field_definitions jsonb NOT NULL, instrument_names jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), cancelled boolean NOT NULL DEFAULT false,
    PRIMARY KEY(scope_id,job_id)
);
CREATE TABLE qtrade_data.collection_part(
    scope_id uuid NOT NULL, part_id uuid NOT NULL, job_id uuid NOT NULL,
    coverage_key text NOT NULL, start_at timestamptz NOT NULL, end_at timestamptz NOT NULL,
    payload jsonb NOT NULL, state text NOT NULL DEFAULT 'queued', attempt integer NOT NULL DEFAULT 0,
    error_code text, next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid, lease_until timestamptz,
    publication jsonb, updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,part_id),
    FOREIGN KEY(scope_id,job_id) REFERENCES qtrade_data.collection_job(scope_id,job_id),
    CHECK(start_at<end_at),
    CHECK(state IN ('queued','running','waiting','failed','completed','no_data','cancelled'))
);
CREATE INDEX collection_part_ready ON qtrade_data.collection_part(next_attempt_at,updated_at)
    WHERE state IN ('queued','waiting','running');
CREATE INDEX collection_part_coverage ON qtrade_data.collection_part(scope_id,coverage_key,start_at,end_at);
CREATE TABLE qtrade_data.collection_job_part(
    scope_id uuid NOT NULL, job_id uuid NOT NULL, part_id uuid NOT NULL, ordinal integer NOT NULL,
    PRIMARY KEY(scope_id,job_id,part_id), UNIQUE(scope_id,job_id,ordinal),
    FOREIGN KEY(scope_id,job_id) REFERENCES qtrade_data.collection_job(scope_id,job_id),
    FOREIGN KEY(scope_id,part_id) REFERENCES qtrade_data.collection_part(scope_id,part_id)
);
CREATE TABLE qtrade_data.collection_delivery(
    scope_id uuid NOT NULL, publication_id uuid NOT NULL, consumer_id text NOT NULL,
    acknowledged_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,publication_id,consumer_id)
);
CREATE TABLE qtrade_data.source_clock(source text PRIMARY KEY, next_at timestamptz NOT NULL);
