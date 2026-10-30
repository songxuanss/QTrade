export type FreshnessPoint = {
  complete_through: string;
  observed_at: string;
  evidence_sha256: string;
};
export type FreshnessView = {
  watch_issue?: string | null;
  snapshot: {
    checked_at: string;
    status: "current" | "within_grace" | "late" | "unknown" | "unavailable";
    stage: "none" | "source" | "collection" | "publication" | "unknown";
    reason: string;
    action: string;
    expectation: {
      complete_through: string;
      available_at: string;
      timezone: string;
      grace_seconds: number;
      basis: string;
    } | null;
    evidence: {
      source_health: "available" | "unknown" | "unavailable";
      source: FreshnessPoint | null;
      collected: FreshnessPoint | null;
      published: FreshnessPoint | null;
    };
  };
  read_at: string;
  age_seconds: number;
  max_age_seconds: number;
  stale: boolean;
  clock_skew: boolean;
};
