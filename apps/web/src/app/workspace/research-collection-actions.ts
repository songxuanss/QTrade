"use server";

import { workspaceRequest } from "../../features/auth/workspace-request";
import type { ResearchPlan } from "../../features/data/research-collection-types";

async function request(path: string, method: "GET" | "POST", body?: unknown) {
  const response = await workspaceRequest(
    `data/collector/research/${path}`,
    method,
    body,
  );
  if (!response) return { error: "unavailable" };
  const value = await response.json().catch(() => null);
  return response.ok
    ? { value }
    : { error: value?.detail?.code ?? "unavailable" };
}
export async function previewResearch(input: {
  source: string;
  kind: string;
  instrument_ids: string[];
  date_axis: string;
  start: string;
  end: string;
  mode: string;
}): Promise<{ value?: ResearchPlan; error?: string }> {
  return request("preview", "POST", input);
}
export async function submitResearch(plan: ResearchPlan, confirmed = false) {
  return request("jobs", "POST", {
    ...plan.request,
    control_sha256: plan.control_sha256,
    confirmed,
  });
}
export async function listResearchJobs() {
  return request("jobs", "GET");
}

export async function readResearchProcessingFailures(id: string) {
  if (!/^[a-f0-9-]{36}$/i.test(id)) return { error: "invalid_input" };
  return request(`jobs/${id}/processing-failures`, "GET");
}

export async function readResearchResponseReceipts(id: string, after?: string) {
  const uuid =
    /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
  if (!uuid.test(id) || (after !== undefined && !uuid.test(after)))
    return { error: "invalid_input" };
  const query = new URLSearchParams({ limit: "20" });
  if (after) query.set("after_id", after);
  try {
    const response = await workspaceRequest(
      `data/collector/research/jobs/${id}/response-receipts`,
      "GET",
      undefined,
      query,
    );
    if (response?.ok) return { value: await response.json() };
    return { error: "unavailable" };
  } catch {
    return { error: "unavailable" };
  }
}

export async function retryResearchJob(id: string) {
  if (!/^[a-f0-9-]{36}$/i.test(id)) return { error: "invalid_input" };
  return request(`jobs/${id}/retry`, "POST");
}
export async function readResearchPublication(id: string) {
  if (!/^[a-f0-9-]{36}$/.test(id)) return { error: "invalid_input" };
  return request(`publications/${id}`, "GET");
}
export async function queryResearch(input: {
  schema_version: 2;
  publication_id: string;
  content_version: string;
  kind: string;
  cursor?: string | null;
  limit: number;
}) {
  return request("records", "POST", input);
}
