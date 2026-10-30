/** Display projection only; collector retains and verifies the full input chain. */
export type ResearchReplayPreview = {
  request_id: string;
  parent_request_id: string;
  control_sha256: string;
  source: string;
  kind: string;
  instrument_ids: string[];
  start: string;
  end: string;
  date_axis: string;
  original_mapping: string;
  target_mapping: string;
  pages: number;
  raw_rows: number;
  limits: { max_pages: number; max_rows: number; max_bytes: number };
  original_collected_at: string[];
};
export type ResearchReplayState = {
  state: string;
  publication_id: string | null;
  error_code: string | null;
};
