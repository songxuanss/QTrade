"use server";

import {
  cancelBacktest as cancelBacktestRequest,
  createBacktestWalkForwardPlan,
  createBacktest as createBacktestRequest,
  createMembership as createMembershipRequest,
  downloadBacktestExport as downloadBacktestExportRequest,
  readBacktestWalkForward as readBacktestWalkForwardRequest,
  startBacktestWalkForward as startBacktestWalkForwardRequest,
} from "../../features/auth/server";

export async function submitBacktest(
  input: Parameters<typeof createBacktestRequest>[0],
) {
  return createBacktestRequest(input);
}

export async function cancelBacktest(runId: string) {
  return cancelBacktestRequest(runId);
}

export async function submitWalkForwardPlan(
  input: Parameters<typeof createBacktestWalkForwardPlan>[0],
) {
  return createBacktestWalkForwardPlan(input);
}

export async function startWalkForward(
  input: Parameters<typeof startBacktestWalkForwardRequest>[0],
) {
  return startBacktestWalkForwardRequest(input);
}

export async function refreshWalkForward(walkForwardId: string) {
  return readBacktestWalkForwardRequest(walkForwardId);
}

export async function exportBacktestRun(runId: string, format: "csv" | "html") {
  return downloadBacktestExportRequest("run", runId, format);
}

export async function exportWalkForward(
  walkForwardId: string,
  format: "csv" | "html",
) {
  return downloadBacktestExportRequest("walk-forward", walkForwardId, format);
}

export async function addMembership(
  input: Parameters<typeof createMembershipRequest>[0],
) {
  return createMembershipRequest(input);
}
