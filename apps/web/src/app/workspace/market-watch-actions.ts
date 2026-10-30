"use server";

import {
  workspaceMarketChart,
  workspaceMarketSnapshots,
} from "../../features/auth/server";

export async function loadMarketSnapshots(instrumentIds: string[]) {
  return workspaceMarketSnapshots(instrumentIds);
}

export async function loadMarketChart(
  instrumentId: string,
  granularity: string,
  days: number,
  datasetId?: string,
) {
  return workspaceMarketChart(instrumentId, granularity, days, datasetId);
}
