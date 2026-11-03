import "server-only";
import { workspaceRequest } from "../auth/workspace-request";

export type CompositionMember = {
  instrument_id: string;
  average_value: string;
  weeks: number;
};

export type CompositionResponse = {
  dataset: {
    dataset_id: string;
    instrument_ids: string[];
    start: string;
    end: string;
    granularity: string;
  };
  snapshot_id: string;
  selected: CompositionMember[];
  excluded: string[];
};

export async function composePoolDataset(input: {
  snapshot_id: string;
  start: string;
  end: string;
  selection_size: number;
  granularity?: "1m" | "5m" | "15m" | "30m" | "1h" | "1d";
  transformation_version?: string;
}) {
  const response = await workspaceRequest(
    "data/datasets/compose",
    "POST",
    {
      ...input,
      granularity: input.granularity ?? "1d",
      transformation_version:
        input.transformation_version ?? "mairui.unadjusted-bars.v1",
    },
    undefined,
    90_000,
  );
  if (!response) return { error: "unavailable" as const };
  if (!response.ok) return { error: "rejected" as const };
  try {
    return { result: (await response.json()) as CompositionResponse };
  } catch {
    return { error: "unavailable" as const };
  }
}