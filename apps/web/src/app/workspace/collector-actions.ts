"use server";

import { isFactorCatalog } from "../../features/data/collector-server";
import { workspaceRequest } from "../../features/auth/workspace-request";
import {
  isUsageSnapshot,
  type UsageSnapshot,
} from "../../features/data/collector-usage-types";
import type {
  CollectorDefaultRange,
  FactorCatalogSnapshot,
  CollectorPreferences,
  CollectorQuality,
  CollectorJob,
  CollectorPreview,
  CollectorRecord,
} from "../../features/data/collector-types";

export async function readCollectorUsage(): Promise<{
  snapshot?: UsageSnapshot;
  error?: string;
}> {
  // The workspace comes from the server session. Usage reads do not contact
  // the supplier or create a request/credit reservation.
  const response = await workspaceRequest("data/collector/usage");
  if (!response?.ok) return { error: await failure(response) };
  try {
    const value: unknown = await response.json();
    return isUsageSnapshot(value)
      ? { snapshot: value }
      : { error: "unverified" };
  } catch {
    return { error: "unavailable" };
  }
}

export async function readCollectorFactorCatalog(): Promise<{
  snapshot?: FactorCatalogSnapshot;
  error?: string;
}> {
  // A status refresh reads this one cache endpoint, not the complete workbench
  // render tree. Authentication and workspace scope remain server-derived.
  const response = await workspaceRequest("data/collector/factor-catalog");
  if (!response?.ok) return { error: await failure(response) };
  try {
    const body: unknown = await response.json();
    return isFactorCatalog(body) ? { snapshot: body } : { error: "unverified" };
  } catch {
    return { error: "unavailable" };
  }
}

export async function readCollectorQuality(
  job: string,
  part: string,
): Promise<{ reports?: CollectorQuality[]; error?: string }> {
  if (![job, part].every((value) => /^[a-f0-9-]{36}$/.test(value)))
    return { error: "invalid_input" };
  const response = await workspaceRequest(
    `data/collector/jobs/${job}/parts/${part}/quality`,
  );
  return response?.ok
    ? { reports: await response.json() }
    : { error: await failure(response) };
}

export async function readCollectorProcessingFailures(
  job: string,
  part: string,
) {
  if (![job, part].every((value) => /^[a-f0-9-]{36}$/.test(value)))
    return { error: "invalid_input" };
  const response = await workspaceRequest(
    `data/collector/jobs/${job}/parts/${part}/processing-failures`,
  );
  return response?.ok
    ? {
        failures:
          (await response.json()) as import("../../features/data/collector-types").CollectorProcessingFailure[],
      }
    : { error: await failure(response) };
}

export async function saveCollectorPreferences(
  input: CollectorPreferences,
): Promise<{ error?: string }> {
  const response = await workspaceRequest(
    "data/collector/preferences",
    "PUT",
    input,
  );
  return response?.ok ? {} : { error: await failure(response) };
}

export async function readCollectorDefaultRange(
  source: string,
  granularity: string,
): Promise<{ range?: CollectorDefaultRange; error?: string }> {
  const response = await workspaceRequest(
    "data/collector/default-range",
    "GET",
    undefined,
    new URLSearchParams({ source, granularity }),
    90_000,
  );
  return response?.ok
    ? { range: await response.json() }
    : { error: await failure(response) };
}

async function failure(response: Response | null): Promise<string> {
  if (!response) return "unavailable";
  const value = await response.json().catch(() => null);
  return typeof value?.detail?.code === "string"
    ? value.detail.code
    : response.status === 403
      ? "forbidden"
      : "unavailable";
}

export async function previewCollector(input: {
  source: string;
  dataset_kind: "bars" | "factors";
  instrument_ids: string[];
  snapshot_ids: string[];
  granularity: string;
  start: string;
  end: string;
  fields: string[];
  mode: "missing" | "all";
}): Promise<{ preview?: CollectorPreview; error?: string }> {
  const response = await workspaceRequest(
    "data/collector/preview",
    "POST",
    input,
    undefined,
    90_000,
  );
  return response?.ok
    ? { preview: await response.json() }
    : { error: await failure(response) };
}
export async function submitCollector(
  preview: CollectorPreview,
  confirmed = false,
): Promise<{ job?: CollectorJob; error?: string }> {
  const response = await workspaceRequest(
    "data/collector/jobs",
    "POST",
    {
      request: preview.request,
      coverage_version: preview.coverage_version,
      confirmed,
    },
    undefined,
    90_000,
  );
  return response?.ok
    ? { job: await response.json() }
    : { error: await failure(response) };
}
export async function readCollectorJob(
  id: string,
  offset = 0,
): Promise<{ job?: CollectorJob; error?: string }> {
  if (!/^[a-f0-9-]{36}$/.test(id)) return { error: "invalid_input" };
  const response = await workspaceRequest(
    `data/collector/jobs/${id}`,
    "GET",
    undefined,
    new URLSearchParams({ offset: String(offset) }),
  );
  return response?.ok
    ? { job: await response.json() }
    : { error: await failure(response) };
}
export async function controlCollector(
  id: string,
  action: "retry" | "cancel",
): Promise<{ error?: string }> {
  if (!/^[a-f0-9-]{36}$/.test(id)) return { error: "invalid_input" };
  const response = await workspaceRequest(
    `data/collector/jobs/${id}/${action}`,
    "POST",
    {},
  );
  return response?.ok ? {} : { error: await failure(response) };
}
export async function readCollectorRecords(
  id: string,
  offset = 0,
): Promise<{
  records?: CollectorRecord[];
  next_offset?: number | null;
  error?: string;
}> {
  if (!/^[a-f0-9-]{36}$/.test(id)) return { error: "invalid_input" };
  const response = await workspaceRequest(
    `data/collector/publications/${id}/records`,
    "GET",
    undefined,
    new URLSearchParams({ offset: String(offset) }),
  );
  return response?.ok
    ? await response.json()
    : { error: await failure(response) };
}
