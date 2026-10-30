import "server-only";
import { workspaceRequest } from "../auth/workspace-request";
export type ResearchDataset = {
  dataset_id: string;
  instrument_ids: string[];
  granularity: string;
  start: string;
  end: string;
  batch_ids: string[];
  transformation_version: string;
};
export async function workspaceResearchDatasets() {
  try {
    const response = await workspaceRequest("data/datasets");
    if (!response?.ok)
      return { datasets: [] as ResearchDataset[], available: false };
    const datasets = await response.json();
    if (!Array.isArray(datasets))
      return { datasets: [] as ResearchDataset[], available: false };
    return { datasets: datasets as ResearchDataset[], available: true };
  } catch {
    return { datasets: [] as ResearchDataset[], available: false };
  }
}
export async function publishResearchDataset(taskId: string) {
  if (!/^[a-f0-9-]{36}$/i.test(taskId)) return "research_invalid";
  const response = await workspaceRequest(
    `data/tasks/${taskId}/dataset`,
    "POST",
    {},
  );
  if (response?.ok) return "research_saved";
  if (response?.status === 403) return "permission";
  if (response?.status === 409) return "research_incomplete";
  if ([400, 422].includes(response?.status ?? 0)) return "research_invalid";
  return "research_error";
}

export async function workspaceDatasetQuality(datasetId: string) {
  if (!/^[a-f0-9-]{36}$/i.test(datasetId)) return null;
  try {
    const response = await workspaceRequest(
      `data/datasets/${datasetId}/quality`,
    );
    if (!response?.ok) return null;
    const report = await response.json();
    if (report?.dataset_id !== datasetId || !Number.isInteger(report.bar_count))
      return null;
    return report as import("./quality-report").CoverageReport;
  } catch {
    return null;
  }
}
