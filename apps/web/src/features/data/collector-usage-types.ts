export type UsageBudget = {
  daily_limit: number;
  credits_used: number;
  active_reservations: number;
  concurrency_limit: number;
};
export type UsageSnapshot = {
  schema_version: 1;
  source: string;
  scope_id: string;
  day: string;
  timezone: string;
  generated_at: string;
  resets_at: string;
  source_budget: UsageBudget;
  scope_budget: UsageBudget;
  request_count: number;
  success_count: number;
  failure_count: number;
  expired_count: number;
  denial_count: number;
  response_bytes: number;
  accounting: "internal_credit_safeguard";
  recent_requests: {
    request_id: string;
    operation: string;
    credits: number;
    reserved_at: string;
    lease_until: string;
    state: "reserved" | "finished" | "expired";
    lease_elapsed: boolean;
    outcome: "success" | "failure" | "expired" | null;
    error_code: string | null;
    duration_ms: number | null;
    response_bytes: number | null;
  }[];
  recent_denials: {
    request_id: string;
    operation: string;
    credits: number;
    reason:
      | "source_daily"
      | "scope_daily"
      | "source_concurrency"
      | "scope_concurrency";
    denied_at: string;
  }[];
};

const record = (v: unknown): v is Record<string, unknown> =>
  Boolean(v && typeof v === "object" && !Array.isArray(v));
const count = (v: unknown): v is number =>
  typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const dated = (v: unknown): v is string =>
  typeof v === "string" && Number.isFinite(Date.parse(v));
const id = (v: unknown): v is string =>
  typeof v === "string" && /^[a-f0-9-]{36}$/i.test(v);
const label = (v: unknown): v is string =>
  typeof v === "string" && /^[a-z][a-z0-9._-]{0,99}$/.test(v);
const budget = (v: unknown): v is UsageBudget =>
  record(v) &&
  count(v.daily_limit) &&
  v.daily_limit > 0 &&
  count(v.credits_used) &&
  count(v.active_reservations) &&
  count(v.concurrency_limit) &&
  v.concurrency_limit > 0;

export function isUsageSnapshot(v: unknown): v is UsageSnapshot {
  if (
    !record(v) ||
    v.schema_version !== 1 ||
    !label(v.source) ||
    !id(v.scope_id) ||
    v.accounting !== "internal_credit_safeguard" ||
    !dated(v.day) ||
    !dated(v.generated_at) ||
    !dated(v.resets_at) ||
    typeof v.timezone !== "string" ||
    !budget(v.scope_budget) ||
    !budget(v.source_budget)
  )
    return false;
  if (
    ![
      "request_count",
      "success_count",
      "failure_count",
      "expired_count",
      "denial_count",
      "response_bytes",
    ].every((k) => count(v[k]))
  )
    return false;
  if (
    !Array.isArray(v.recent_requests) ||
    v.recent_requests.length > 50 ||
    !Array.isArray(v.recent_denials) ||
    v.recent_denials.length > 50
  )
    return false;
  return (
    v.recent_requests.every(
      (r) =>
        record(r) &&
        id(r.request_id) &&
        label(r.operation) &&
        count(r.credits) &&
        r.credits > 0 &&
        dated(r.reserved_at) &&
        dated(r.lease_until) &&
        ["reserved", "finished", "expired"].includes(String(r.state)) &&
        typeof r.lease_elapsed === "boolean" &&
        [null, "success", "failure", "expired"].includes(
          r.outcome as string | null,
        ) &&
        [
          null,
          "timeout",
          "unavailable",
          "unauthorized",
          "rate_limited",
          "unverified",
        ].includes(r.error_code as string | null) &&
        (r.duration_ms === null || count(r.duration_ms)) &&
        (r.response_bytes === null || count(r.response_bytes)),
    ) &&
    v.recent_denials.every(
      (r) =>
        record(r) &&
        id(r.request_id) &&
        label(r.operation) &&
        count(r.credits) &&
        r.credits > 0 &&
        dated(r.denied_at) &&
        [
          "source_daily",
          "scope_daily",
          "source_concurrency",
          "scope_concurrency",
        ].includes(String(r.reason)),
    )
  );
}
