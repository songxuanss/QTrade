"use server";

import { workspaceRequest } from "../../features/auth/workspace-request";
import type { FreshnessView } from "../../features/data/freshness-types";

export async function readFreshness(publication: string): Promise<{
  view?: FreshnessView;
  error?: string;
}> {
  return requestFreshness(publication, false);
}

export async function checkFreshness(publication: string): Promise<{
  view?: FreshnessView;
  error?: string;
}> {
  return requestFreshness(publication, true);
}

async function requestFreshness(
  publication: string,
  check: boolean,
): Promise<{
  view?: FreshnessView;
  error?: string;
}> {
  if (!/^[a-f0-9-]{36}$/.test(publication)) return { error: "invalid_input" };
  try {
    const response = await workspaceRequest(
      `data/collector/publications/${publication}/freshness${check ? "/check" : ""}`,
      check ? "POST" : "GET",
      undefined,
      undefined,
      check ? 90_000 : 10_000,
    );
    if (response?.ok) return { view: await response.json() };
    if (!response) return { error: "unavailable" };
    return { error: (await response.json()).detail?.code ?? "unavailable" };
  } catch {
    return { error: "unavailable" };
  }
}
