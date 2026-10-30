"use server";

import { workspaceRequest } from "../../features/auth/workspace-request";
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
async function request(path: string, method: "GET" | "POST", body?: unknown) {
  try {
    const response = await workspaceRequest(
      `data/collector/${path}`,
      method,
      body,
      undefined,
      90000,
    );
    const value = await response?.json().catch(() => null);
    if (!response?.ok || !value)
      return { error: String(value?.detail?.code ?? "unavailable") };
    return { value };
  } catch {
    return { error: "unavailable" };
  }
}
export async function listCollectionInputs(job: string, part: string) {
  if (!uuid.test(job) || !uuid.test(part)) return { error: "invalid_input" };
  const result = await request(
    `jobs/${job}/parts/${part}/normalization-inputs`,
    "GET",
  );
  if (result.error) return { error: result.error };
  return {
    value: result.value.inputs.map(
      (v: {
        input_id: string;
        input_sha256: string;
        prepared_at: string;
        row_count: number;
        mapping_version: string;
      }) => ({
        input_id: v.input_id,
        input_sha256: v.input_sha256,
        prepared_at: v.prepared_at,
        row_count: v.row_count,
        mapping_version: v.mapping_version,
      }),
    ) as import("../../features/data/collection-replay-types").CollectionInputChoice[],
  };
}
function projection(v: {
  command: {
    request_id: string;
    parent_job_id: string;
    parent_part_id: string;
    input_id: string;
  };
  input: {
    input_sha256: string;
    instrument_id: string;
    instrument_name: string;
    source: string;
    field: string | null;
    start: string;
    end: string;
    row_count: number;
    mapping_version: string;
  };
  control_sha256: string;
  target_mapping_version: string;
  rule_version: string;
  verified_bytes: number;
  timezone: string;
  sources: { kind: string; captured_at: string }[];
  limits: { max_rows: number; max_bytes: number; max_receipts: number };
}): import("../../features/data/collection-replay-types").CollectionReplayView {
  return {
    request_id: v.command.request_id,
    parent_job_id: v.command.parent_job_id,
    parent_part_id: v.command.parent_part_id,
    input_id: v.command.input_id,
    control_sha256: v.control_sha256,
    input_sha256: v.input.input_sha256,
    instrument_id: v.input.instrument_id,
    instrument_name: v.input.instrument_name,
    source: v.input.source,
    field: v.input.field,
    start: v.input.start,
    end: v.input.end,
    row_count: v.input.row_count,
    original_mapping: v.input.mapping_version,
    target_mapping: v.target_mapping_version,
    rule_version: v.rule_version,
    verified_bytes: v.verified_bytes,
    timezone: v.timezone,
    original_acquired_at: [
      ...new Set(
        v.sources
          .filter((s) => ["history", "factor_history"].includes(s.kind))
          .map((s) => s.captured_at),
      ),
    ],
    limits: v.limits,
  };
}
export async function previewCollectionReplay(
  parent: string,
  part: string,
  input: string,
  target: string,
) {
  if (![parent, part, input, target].every((v) => uuid.test(v)))
    return { error: "invalid_input" };
  const result = await request("replays/preview", "POST", {
    request_id: target,
    parent_job_id: parent,
    parent_part_id: part,
    input_id: input,
  });
  return result.error
    ? { error: result.error }
    : { value: projection(result.value) };
}
export async function readCollectionReplay(target: string) {
  if (!uuid.test(target)) return { error: "invalid_input" };
  const result = await request(`replays/${target}`, "GET");
  return result.error
    ? { error: result.error }
    : { value: projection(result.value) };
}
export async function submitCollectionReplay(
  parent: string,
  part: string,
  input: string,
  target: string,
  control: string,
) {
  if (
    ![parent, part, input, target].every((v) => uuid.test(v)) ||
    !/^[a-f0-9]{64}$/.test(control)
  )
    return { error: "invalid_input" };
  const result = await request("replays", "POST", {
    request_id: target,
    parent_job_id: parent,
    parent_part_id: part,
    input_id: input,
    control_sha256: control,
  });
  return result.error
    ? { error: result.error }
    : { value: { state: String(result.value.state) } };
}
export async function readCollectionReplayState(target: string) {
  if (!uuid.test(target)) return { error: "invalid_input" };
  const result = await request(`jobs/${target}`, "GET");
  return result.error
    ? { error: result.error }
    : { value: { state: String(result.value.state) } };
}
