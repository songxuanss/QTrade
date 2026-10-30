export type CampaignSelection = {
  snapshot_ids: string[];
  instrument_ids?: string[];
  request_id: string;
  granularity: string;
  start: string;
  end: string;
  mode: "missing" | "all";
};
export type CampaignPlan = {
  plan_id: string;
  coverage_version: string;
  planned_parts: number;
  selection: CampaignSelection;
  members: {
    instrument: { instrument_id: string; name: string };
    affiliations: string[];
    planned_parts: number;
    reused_task_ids: string[];
    existing_ranges: { start: string; end: string }[];
    submit_ranges: { start: string; end: string }[];
  }[];
};
export type CampaignRun = {
  campaign_id: string;
  state: string;
  granularity: string;
  start: string;
  end: string;
  mode: string;
  member_count: number;
  planned_parts: number;
  expanded_parts: number;
  collected_parts: number;
  failed_parts: number;
  viewable_members: number;
  no_observation_members: number;
  no_observation_parts?: number;
  member_offset: number;
  members: {
    instrument_id: string;
    name: string;
    state: string;
    viewable: boolean;
    planned_parts: number;
    expanded_parts: number;
    parts: {
      state: string;
      task_id: string;
      start: string;
      end: string;
      error_code?: string;
      raw_records?: number;
    }[];
    parts_truncated: boolean;
  }[];
};
