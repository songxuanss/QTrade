export type CollectionInputChoice = {
  input_id: string;
  input_sha256: string;
  prepared_at: string;
  row_count: number;
  mapping_version: string;
};
export type CollectionReplayView = {
  request_id: string;
  parent_job_id: string;
  parent_part_id: string;
  input_id: string;
  control_sha256: string;
  input_sha256: string;
  instrument_id: string;
  instrument_name: string;
  source: string;
  field: string | null;
  start: string;
  end: string;
  row_count: number;
  original_mapping: string;
  target_mapping: string;
  rule_version: string;
  verified_bytes: number;
  timezone: string;
  original_acquired_at: string[];
  limits: { max_rows: number; max_bytes: number; max_receipts: number };
};
