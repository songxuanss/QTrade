"use server";

import { workspaceRequest } from "../../features/auth/workspace-request";
import type {
  ScheduleDraft,
  ScheduleRecord,
} from "../../features/data/schedule-types";

async function error(response: Response | null): Promise<string> {
  if (!response) return "unavailable";
  if (response.status === 422) return "invalid_input";
  try {
    return (await response.json()).detail?.code ?? "unavailable";
  } catch {
    return "unavailable";
  }
}

export async function readSchedules(
  offset = 0,
): Promise<{ records?: ScheduleRecord[]; error?: string }> {
  const response = await workspaceRequest(
    "data/collector/schedules",
    "GET",
    undefined,
    new URLSearchParams({ offset: String(offset) }),
  );
  return response?.ok
    ? { records: await response.json() }
    : { error: await error(response) };
}

export async function createSchedule(
  draft: ScheduleDraft,
): Promise<{ record?: ScheduleRecord; error?: string }> {
  const response = await workspaceRequest(
    "data/collector/schedules",
    "POST",
    draft,
    undefined,
    90000,
  );
  return response?.ok
    ? { record: await response.json() }
    : { error: await error(response) };
}

export async function controlSchedule(
  id: string,
  revision: string,
  action: "enable" | "disable",
) {
  if (!/^[a-f0-9-]{36}$/.test(id) || !/^[a-f0-9]{64}$/.test(revision))
    return { error: "invalid_input" };
  const response = await workspaceRequest(
    `data/collector/schedules/${id}/${action}`,
    "POST",
    { revision },
    undefined,
    90000,
  );
  return response?.ok ? {} : { error: await error(response) };
}
