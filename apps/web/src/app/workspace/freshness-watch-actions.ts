"use server";

import { workspaceRequest } from "../../features/auth/workspace-request";
import type { FreshnessWatch } from "../../features/data/freshness-watch-panel";

async function request(
  path: string,
  method: "GET" | "POST",
  body?: unknown,
): Promise<{ watch?: FreshnessWatch; error?: string }> {
  try {
    const response = await workspaceRequest(
      `data/collector/${path}`,
      method,
      body,
    );
    if (response?.ok) return { watch: await response.json() };
    return {
      error: response
        ? ((await response.json()).detail?.code ?? "unavailable")
        : "unavailable",
    };
  } catch {
    return { error: "unavailable" };
  }
}
export async function readFreshnessWatch(publication: string) {
  if (!/^[a-f0-9-]{36}$/.test(publication)) return { error: "invalid_input" };
  return request(`publications/${publication}/freshness/watch`, "GET");
}
export async function createFreshnessWatch(publication: string) {
  if (!/^[a-f0-9-]{36}$/.test(publication)) return { error: "invalid_input" };
  return request(`publications/${publication}/freshness/watch`, "POST");
}
export async function controlFreshnessWatch(
  watch: string,
  revision: number,
  enabled: boolean,
) {
  if (!/^[a-f0-9-]{36}$/.test(watch)) return { error: "invalid_input" };
  return request(`freshness/watches/${watch}/control`, "POST", {
    revision,
    enabled,
  });
}
