import "server-only";
import { workspaceRequest } from "../auth/workspace-request";

export type RiskSwitch = {
  active: boolean;
  reason: string | null;
  revision: number;
  changed_at: string | null;
};
export type RiskLimit = {
  limit_id: string;
  dimension: string;
  scope_id: string;
  metric: string;
  maximum: string;
  version: number;
};
export type RiskDecision = {
  decision: {
    decision_id: string;
    outcome: string;
    decided_at: string;
    reasons: { code: string; message: string }[];
  };
};
export type Recovery = {
  request_id: string;
  account_id: string | null;
  requested_at: string;
  self_requested: boolean;
};

export async function workspaceRisk() {
  const empty = {
    switchState: null as RiskSwitch | null,
    limits: [] as RiskLimit[],
    decisions: [] as RiskDecision[],
    recoveries: [] as Recovery[],
    error: true,
  };
  try {
    const responses = await Promise.all(
      [
        "kill-switch",
        "risk/limits",
        "risk/decisions",
        "kill-switch/recovery",
      ].map((path) => workspaceRequest(path)),
    );
    if (responses.some((response) => !response?.ok)) return empty;
    const [state, limits, decisions, recoveries] = await Promise.all(
      responses.map((response) => response!.json()),
    );
    if (
      typeof state?.active !== "boolean" ||
      !Number.isInteger(state.revision) ||
      !Array.isArray(limits) ||
      !Array.isArray(decisions) ||
      !Array.isArray(recoveries)
    )
      return empty;
    return {
      switchState: state as RiskSwitch,
      limits: limits as RiskLimit[],
      decisions: decisions as RiskDecision[],
      recoveries: recoveries as Recovery[],
      error: false,
    };
  } catch {
    return empty;
  }
}

export async function mutateRisk(path: string, body: unknown) {
  const response = await workspaceRequest(path, "POST", body);
  if (response?.ok) return "success";
  if (response?.status === 403) return "denied";
  if (response?.status === 409) return "conflict";
  return "error";
}
