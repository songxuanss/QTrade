"use server";

import { workspaceRequest } from "../../features/auth/workspace-request";
import type {
  ResearchReplayPreview,
  ResearchReplayState,
} from "../../features/data/research-replay-types";

const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
async function request(path: string, method: "GET" | "POST", body?: unknown) {
  try {
    const response = await workspaceRequest(
      `data/collector/research/${path}`,
      method,
      body,
      undefined,
      90_000,
    );
    const value = await response?.json().catch(() => null);
    if (!response?.ok || !value)
      return { error: String(value?.detail?.code ?? "unavailable") };
    return { value };
  } catch {
    return { error: "unavailable" };
  }
}
function project(value: {
  command: { request_id: string; parent_request_id: string };
  control_sha256: string;
  parent_plan: { capability: { mapping_version: string } };
  target_plan: {
    request: {
      source: string;
      kind: string;
      instrument_ids: string[];
      start: string;
      end: string;
      date_axis: string;
    };
    capability: { mapping_version: string };
  };
  source_batches: { raw_record_count: number; collected_at: string }[];
  limits: ResearchReplayPreview["limits"];
}): ResearchReplayPreview {
  return {
    request_id: value.command.request_id,
    parent_request_id: value.command.parent_request_id,
    control_sha256: value.control_sha256,
    source: value.target_plan.request.source,
    kind: value.target_plan.request.kind,
    instrument_ids: value.target_plan.request.instrument_ids,
    start: value.target_plan.request.start,
    end: value.target_plan.request.end,
    date_axis: value.target_plan.request.date_axis,
    original_mapping: value.parent_plan.capability.mapping_version,
    target_mapping: value.target_plan.capability.mapping_version,
    pages: value.source_batches.length,
    raw_rows: value.source_batches.reduce(
      (sum, batch) => sum + batch.raw_record_count,
      0,
    ),
    limits: value.limits,
    original_collected_at: [
      ...new Set(value.source_batches.map((b) => b.collected_at)),
    ],
  };
}
export async function previewResearchReplay(
  parent: string,
  target: string,
): Promise<{ value?: ResearchReplayPreview; error?: string }> {
  if (!uuid.test(parent) || !uuid.test(target))
    return { error: "invalid_input" };
  const result = await request("replays/preview", "POST", {
    request_id: target,
    parent_request_id: parent,
  });
  if (result.error) return { error: result.error };
  return { value: project(result.value) };
}
export async function readResearchReplay(
  target: string,
): Promise<{ value?: ResearchReplayPreview; error?: string }> {
  if (!uuid.test(target)) return { error: "invalid_input" };
  const result = await request(`replays/${target}`, "GET");
  if (result.error) return { error: result.error };
  return { value: project(result.value) };
}
export async function submitResearchReplay(
  parent: string,
  target: string,
  control: string,
): Promise<{ value?: ResearchReplayState; error?: string }> {
  if (
    !uuid.test(parent) ||
    !uuid.test(target) ||
    !/^[a-f0-9]{64}$/.test(control)
  )
    return { error: "invalid_input" };
  return request("replays", "POST", {
    request_id: target,
    parent_request_id: parent,
    control_sha256: control,
  });
}
export async function readResearchReplayState(
  target: string,
): Promise<{ value?: ResearchReplayState; error?: string }> {
  if (!uuid.test(target)) return { error: "invalid_input" };
  return request(`jobs/${target}`, "GET");
}
