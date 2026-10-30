"use server";

import { workspaceRequest } from "../../features/auth/workspace-request";
import type {
  CampaignPlan,
  CampaignRun,
  CampaignSelection,
} from "../../features/data/campaign-types";

async function error(response: Response | null) {
  if (!response) return "unavailable";
  const body = await response.json().catch(() => null);
  return response.status === 403
    ? "forbidden"
    : response.status === 409
      ? "conflict"
      : String(body?.detail?.code ?? "unavailable");
}
export async function previewCampaign(
  body: CampaignSelection,
): Promise<{ plan?: CampaignPlan; error?: string }> {
  const response = await workspaceRequest(
    "data/campaigns/preview",
    "POST",
    body,
    undefined,
    90_000,
  );
  return response?.ok
    ? { plan: await response.json() }
    : { error: await error(response) };
}
export async function submitCampaign(
  planId: string,
  coverageVersion: string,
): Promise<{ campaign_id?: string; error?: string }> {
  const response = await workspaceRequest(
    "data/campaigns",
    "POST",
    { plan_id: planId, coverage_version: coverageVersion },
    undefined,
    90_000,
  );
  return response?.ok
    ? await response.json()
    : { error: await error(response) };
}
export async function readCampaign(
  id: string,
  memberOffset = 0,
): Promise<{ run?: CampaignRun; error?: string }> {
  if (!/^[a-f0-9-]{36}$/.test(id)) return { error: "invalid_input" };
  const response = await workspaceRequest(
    `data/campaigns/${id}`,
    "GET",
    undefined,
    new URLSearchParams({ member_offset: String(memberOffset) }),
  );
  return response?.ok
    ? { run: await response.json() }
    : { error: await error(response) };
}
export async function readCampaigns(): Promise<CampaignRun[]> {
  const response = await workspaceRequest("data/campaigns");
  return response?.ok ? await response.json() : [];
}
export async function controlCampaign(
  id: string,
  action: "retry" | "cancel",
): Promise<{ error?: string }> {
  if (!/^[a-f0-9-]{36}$/.test(id)) return { error: "invalid_input" };
  const response = await workspaceRequest(
    `data/campaigns/${id}/${action}`,
    "POST",
    {},
  );
  return response?.ok ? {} : { error: await error(response) };
}
