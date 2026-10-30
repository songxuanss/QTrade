"use server";
import { workspaceRequest } from "../../features/auth/workspace-request";
import type { HealthPage } from "../../features/data/freshness-health-types";
export async function readFreshnessHealth(
  after?: string,
): Promise<{ page?: HealthPage; error?: string }> {
  if (after !== undefined && !/^[a-f0-9]{64}$/.test(after))
    return { error: "invalid_input" };
  const query = new URLSearchParams({ limit: "20" });
  if (after) query.set("after_key", after);
  try {
    const response = await workspaceRequest(
      "data/collector/freshness/health",
      "GET",
      undefined,
      query,
    );
    if (response?.ok) return { page: await response.json() };
    return {
      error: response
        ? ((await response.json()).detail?.code ?? "unavailable")
        : "unavailable",
    };
  } catch {
    return { error: "unavailable" };
  }
}
