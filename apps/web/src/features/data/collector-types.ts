export type CollectorProcessingFailure = {
  failure_id: string;
  checker_version: string;
  mapping_version: string;
  stage: "normalization";
  status: "blocked";
  normalized_record_count: null;
  raw_capture_ids: string[];
  attempt: number;
  failed_at: string;
};

export type CollectorSource = {
  source: string;
  name: string;
  provider: string;
  dataset_kind: "bars" | "factors";
  periods: string[];
  instrument_kinds: string[];
  markets: string[];
  timezone: string;
  configured: boolean;
  evidence: string;
  limitations: string[];
  /** Commercial tier from the collector catalog; paid sources win defaults. */
  tier?: "paid" | "free";
};
export type FactorDefinition = {
  factor_id: string;
  name: string;
  category: string;
  unit: string;
  calc_version: string;
  description: string;
  update_frequency: string;
  available: boolean;
  unavailable_reason: string | null;
};
export type FactorCatalogSnapshot = {
  schema_version: 1;
  source: "mairui.factor.history";
  status: "fresh" | "stale" | "loading" | "unavailable" | "unconfigured";
  definitions: FactorDefinition[];
  content_sha256: string | null;
  fetched_at: string | null;
  last_attempt_at: string | null;
  refreshing: boolean;
  error_code: string | null;
  permission_evidence: "not_established_by_catalog";
};
export type CollectorRequest = {
  schema_version: number;
  scope_id: string;
  actor_id: string;
  correlation_id: string;
  request_id: string;
  source: string;
  dataset_kind: "bars" | "factors";
  instrument_ids: string[];
  granularity: string;
  start: string;
  end: string;
  fields: string[];
  mode: "missing" | "all";
};
export type CollectorPreview = {
  confirmation_required?: boolean;
  plan_guard?: {
    version: string;
    max_instruments: number;
    max_new_parts: number;
    confirm_new_parts: number;
    confirm_credits: number;
  } | null;
  execution_estimate?: {
    calls_min: number;
    calls_max: number;
    credits_min: number;
    credits_max: number;
    basis: string;
    accounting: string;
    includes_retries: false;
  } | null;
  request: CollectorRequest;
  mapping_version: string;
  coverage_version: string;
  planned_parts: number;
  completed_parts: number;
  active_parts: number;
  missing_parts: number;
  instrument_names: Record<string, string>;
  field_definitions: FactorDefinition[];
  limitations: string[];
};
export type CollectorPublication = {
  publication_id: string;
  dataset_kind: "bars" | "factors";
  instrument_id: string;
  instrument_name: string;
  granularity: string;
  field: string | null;
  record_count: number;
  raw_record_count: number;
  raw_sha256: string;
  records_sha256: string;
  limitations: string[];
  // Optional projection keeps older immutable manifests readable.
  evidence?: {
    opening_boundary_semantics?: "unverified" | "not_observed";
    opening_boundary_source_labels?: string[];
  };
};
export type CollectorPart = {
  execution_kind?: "collection" | "normalization_replay";
  part_id: string;
  instrument_id: string;
  instrument_name: string;
  field: string | null;
  start: string;
  end: string;
  state: string;
  attempt: number;
  error_code: string | null;
  wait_reason?:
    | "source_daily"
    | "scope_daily"
    | "source_concurrency"
    | "scope_concurrency"
    | null;
  next_attempt_at: string;
  publication: CollectorPublication | null;
  consumer?: {
    state: string;
    error_code: string | null;
    dataset_id?: string | null;
  };
};
export type CollectorJob = {
  job_id: string;
  state: string;
  request: CollectorRequest;
  created_at: string;
  counts: Record<string, number>;
  total_parts: number;
  offset: number;
  next_offset: number | null;
  instrument_names: Record<string, string>;
  field_definitions: FactorDefinition[];
  parts: CollectorPart[];
};
export type CollectorRecord = {
  instrument_id: string;
  timestamp?: string;
  open?: string;
  close?: string;
  volume?: string;
  factor_id?: string;
  trade_date?: string;
  value?: string | null;
  value_kind?: string;
  missing_reason?: string | null;
  calc_version?: string;
};
export type CollectorDefaultRange = {
  schema_version: 1;
  source: string;
  granularity: string;
  first: string;
  last: string;
  start: string;
  end: string;
  timezone: string;
  expected_available_at: string;
  basis: string;
  endpoint_semantics: "start_inclusive_end_exclusive";
  availability: "estimated_not_verified";
};
export type CollectorPreferences = {
  schema_version: 1;
  dataset_kind: "bars" | "factors";
  source: string;
  granularity: string;
  first: string | null;
  last: string | null;
  instruments: string;
  snapshot_id: string | null;
  snapshot_ids?: string[];
  fields: string[];
  mode: "missing" | "all";
};
export type CollectorQuality = {
  quality_id: string;
  rule_version: string;
  checked_at: string;
  record_count: number;
  status: "passed" | "warning" | "blocked" | "no_observations";
  issues: {
    code: string;
    severity: "block" | "warning";
    affected_records: number;
  }[];
};
