import type { FreshnessView } from "./freshness-types";
import type { FreshnessWatch } from "./freshness-watch-panel";
export type HealthEntry = {
  key_sha256: string;
  key: {
    source: string;
    instrument_id: string;
    dataset_kind: string;
    period: string | null;
    field: string | null;
    adjustment: string | null;
    mapping_version: string;
  };
  instrument_name: string;
  snapshot: FreshnessView["snapshot"] | null;
  watch: (FreshnessWatch & { last_attempt_at: string | null }) | null;
  last_notice: {
    kind: "opened" | "recovered";
    notice_id: string;
    occurred_at: string;
  } | null;
  consumer_state:
    "not_applicable" | "not_claimed" | "pending" | "leased" | "delivered";
  consumer_lease_expired: boolean;
  source_configuration: "configured" | "unconfigured" | "unknown";
  effective_status: string;
  age_seconds: number | null;
};
export type HealthPage = {
  scope_id: string;
  read_at: string;
  max_age_seconds: number;
  consumer_id: string;
  entries: HealthEntry[];
  next_key: string | null;
};
