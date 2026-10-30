export type ResearchCapability = {
  source: string;
  name: string;
  kind: string;
  configured: boolean;
  evidence: string;
  limitations: string[];
  supports_as_of: boolean;
};
export type ResearchRequest = {
  schema_version: 2;
  actor_id: string;
  scope_id: string;
  request_id: string;
  correlation_id: string;
  source: string;
  kind: string;
  instrument_ids: string[];
  date_axis: string;
  start: string;
  end: string;
  mode: string;
};
export type ResearchPlan = {
  control_sha256: string;
  confirmation_required: boolean;
  execution_estimate: NonNullable<
    import("./collector-types").CollectorPreview["execution_estimate"]
  >;
  plan_guard: NonNullable<
    import("./collector-types").CollectorPreview["plan_guard"]
  >;
  request: ResearchRequest;
  capability: ResearchCapability;
  partitions: { instrument_id: string; start: string; end: string }[];
  estimated_calls_min: number;
  estimated_calls_max: number | null;
  estimate_basis: string;
};
export type ResearchJob = {
  request_id: string;
  request: ResearchRequest;
  state: string;
  attempts: number;
  publication_id: string | null;
  error_code: string | null;
  wait_reason?: import("./budget-wait").BudgetReason | null;
  next_attempt_at?: string;
};
