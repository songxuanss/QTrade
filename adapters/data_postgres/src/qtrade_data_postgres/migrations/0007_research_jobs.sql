-- Research queue is separate from immutable input/evidence and quality publication.
CREATE TABLE qtrade_data.research_job(
 scope_id uuid NOT NULL, request_id uuid NOT NULL,
 state text NOT NULL DEFAULT 'queued'
 CHECK(state IN ('queued','running','completed','no_observations','failed')),
 lease_id uuid, lease_until timestamptz, attempts integer NOT NULL DEFAULT 0,
 publication_id uuid, error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(scope_id,request_id),
 FOREIGN KEY(scope_id,request_id) REFERENCES qtrade_data.research_run(scope_id,request_id),
 FOREIGN KEY(scope_id,publication_id) REFERENCES qtrade_data.research_publication(scope_id,publication_id),
 CHECK((state='running')=(lease_id IS NOT NULL AND lease_until IS NOT NULL)),
 CHECK((state='completed')=(publication_id IS NOT NULL))
);
CREATE INDEX research_job_claim ON qtrade_data.research_job(state,created_at);
