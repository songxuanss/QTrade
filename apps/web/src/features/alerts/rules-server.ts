import "server-only";
import { workspaceRequest } from "../auth/workspace-request";

export type AlertRule = {
  rule_id: string;
  version: number;
  name: string;
  source: string;
  condition: string;
  severity: string;
  cooldown_seconds: number;
  enabled: boolean;
};
export async function workspaceAlertRules() {
  try {
    const response = await workspaceRequest("alert-rules");
    if (!response?.ok) return { rules: [] as AlertRule[], error: true };
    const data = await response.json();
    if (!Array.isArray(data)) return { rules: [] as AlertRule[], error: true };
    return { rules: data as AlertRule[], error: false };
  } catch {
    return { rules: [] as AlertRule[], error: true };
  }
}
export async function mutateAlertRule(
  path: string,
  method: "POST" | "PATCH",
  body: unknown,
) {
  const response = await workspaceRequest(path, method, body);
  if (response?.ok)
    return method === "POST" && path === "alert-evaluations"
      ? "queued"
      : "success";
  if (response?.status === 403) return "denied";
  if (response?.status === 400 || response?.status === 422) return "invalid";
  if (response?.status === 409) return "conflict";
  return "error";
}
