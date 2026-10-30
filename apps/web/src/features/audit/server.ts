import "server-only";
import { workspaceRequest } from "../auth/workspace-request";
export const auditFilterNames = [
  "start",
  "end",
  "actor_id",
  "action",
  "target_id",
  "result",
  "trace_id",
  "before",
] as const;
export type AuditFilters = Partial<
  Record<(typeof auditFilterNames)[number], string>
>;
export type AuditEvent = {
  event_id: number;
  occurred_at: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  result: string;
  trace_id: string;
  source: string;
  source_network: string | null;
  details: { target_id?: string; permission?: string; reason?: string };
};
export function auditParams(filters: AuditFilters) {
  const params = new URLSearchParams();
  for (const key of auditFilterNames)
    if (filters[key]) params.set(key, filters[key]);
  return params;
}
export async function workspaceAudit(filters: AuditFilters) {
  const response = await workspaceRequest(
    "audit",
    "GET",
    undefined,
    auditParams(filters),
  );
  if (!response?.ok)
    return {
      events: [] as AuditEvent[],
      next_before: null as number | null,
      error:
        response?.status === 400 || response?.status === 422
          ? "invalid"
          : "unavailable",
    };
  try {
    const data = await response.json();
    if (!Array.isArray(data.events)) throw Error("invalid audit response");
    return {
      events: data.events as AuditEvent[],
      next_before: data.next_before as number | null,
      error: null,
    };
  } catch {
    return {
      events: [] as AuditEvent[],
      next_before: null as number | null,
      error: "unavailable",
    };
  }
}
