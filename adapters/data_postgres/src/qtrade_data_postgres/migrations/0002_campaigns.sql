CREATE TABLE qtrade_data.campaign_plan (
    scope_id uuid NOT NULL, plan_id uuid NOT NULL, created_at timestamptz NOT NULL,
    payload jsonb NOT NULL, PRIMARY KEY(scope_id,plan_id),
    CHECK((payload->>'scope_id') IS NOT DISTINCT FROM scope_id::text),
    CHECK((payload->>'plan_id') IS NOT DISTINCT FROM plan_id::text)
);
CREATE TRIGGER campaign_plan_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
    ON qtrade_data.campaign_plan FOR EACH STATEMENT EXECUTE FUNCTION qtrade_data.immutable_snapshot();
CREATE TABLE qtrade_data.campaign_run (
    scope_id uuid NOT NULL, campaign_id uuid NOT NULL, plan_id uuid NOT NULL,
    request_id uuid NOT NULL, actor_id uuid NOT NULL, created_at timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','partial','completed','failed','cancelled')),
    version integer NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(scope_id,campaign_id), UNIQUE(scope_id,request_id),
    FOREIGN KEY(scope_id,plan_id) REFERENCES qtrade_data.campaign_plan(scope_id,plan_id)
);
CREATE TABLE qtrade_data.campaign_work (
    scope_id uuid NOT NULL, campaign_id uuid NOT NULL, ordinal integer NOT NULL CHECK(ordinal>=0),
    instrument_id text NOT NULL, task_id uuid NOT NULL, start_at timestamptz NOT NULL,
    end_at timestamptz NOT NULL, result jsonb NOT NULL DEFAULT '{"state":"queued"}',
    checked_at timestamptz NOT NULL DEFAULT 'epoch',
    PRIMARY KEY(scope_id,campaign_id,ordinal), UNIQUE(scope_id,campaign_id,task_id),
    FOREIGN KEY(scope_id,campaign_id) REFERENCES qtrade_data.campaign_run(scope_id,campaign_id)
);
CREATE INDEX campaign_run_page ON qtrade_data.campaign_run(scope_id,created_at DESC,campaign_id DESC);
CREATE INDEX campaign_work_poll ON qtrade_data.campaign_work(scope_id,campaign_id,checked_at,ordinal);
ALTER TABLE qtrade_data.campaign_plan ENABLE ROW LEVEL SECURITY;
ALTER TABLE qtrade_data.campaign_plan FORCE ROW LEVEL SECURITY;
CREATE POLICY campaign_plan_scope ON qtrade_data.campaign_plan USING(scope_id=NULLIF(current_setting('qtrade_data.scope_id',true),'')::uuid) WITH CHECK(scope_id=NULLIF(current_setting('qtrade_data.scope_id',true),'')::uuid);
ALTER TABLE qtrade_data.campaign_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE qtrade_data.campaign_run FORCE ROW LEVEL SECURITY;
CREATE POLICY campaign_run_scope ON qtrade_data.campaign_run USING(scope_id=NULLIF(current_setting('qtrade_data.scope_id',true),'')::uuid) WITH CHECK(scope_id=NULLIF(current_setting('qtrade_data.scope_id',true),'')::uuid);
ALTER TABLE qtrade_data.campaign_work ENABLE ROW LEVEL SECURITY;
ALTER TABLE qtrade_data.campaign_work FORCE ROW LEVEL SECURITY;
CREATE POLICY campaign_work_scope ON qtrade_data.campaign_work USING(scope_id=NULLIF(current_setting('qtrade_data.scope_id',true),'')::uuid) WITH CHECK(scope_id=NULLIF(current_setting('qtrade_data.scope_id',true),'')::uuid);
