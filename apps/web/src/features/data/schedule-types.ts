export type ScheduleDraft = {
  schedule_id: string;
  name: string;
  instrument_ids: string[];
  snapshot_id?: string;
  local_time: string;
  cadence: "daily" | "weekly" | "quarterly";
  weekday: number;
  timezone: string;
  lookback_days: number;
  catchup_days: number;
  approved_parts: number;
  approved_credits: number;
  automation_confirmed: boolean;
};
export type ScheduleRecord = {
  revision: string;
  state: "disabled" | "enabled" | "paused";
  pause_reason:
    "authorization" | "source_unavailable" | "approval_required" | null;
  next_due_at: string;
  draft: Omit<ScheduleDraft, "instrument_ids" | "snapshot_id"> & {
    actor_id: string;
    selection: {
      source: string;
      instrument_ids: string[];
      membership_snapshot_id?: string;
    };
  };
};
