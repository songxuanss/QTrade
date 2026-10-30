import "server-only";
import { workspaceRequest } from "../auth/workspace-request";

export type UniverseDefinition = {
  universe_id: string;
  name: string;
  category: "industry" | "theme" | "index_members";
  source_status: "available" | "ambiguous";
};
export type UniverseSnapshot = {
  snapshot_id: string;
  universe_id: string;
  version: number;
  name: string;
  observed_at: string;
  market_scope: string[];
  content_sha256: string;
  members: {
    instrument: { instrument_id: string; name: string; exchange: string };
    affiliations: string[];
  }[];
  reference_issues: {
    instrument_id: string;
    code: "directory_member_missing" | "name_conflict";
    directory_name: string | null;
    member_name: string;
  }[];
};

export async function workspaceUniverseCatalog(
  search: string,
  category: string,
  offset: number,
) {
  const query = new URLSearchParams({
    search: search.slice(0, 100),
    offset: String(offset),
    limit: "12",
  });
  if (["industry", "theme", "index_members"].includes(category))
    query.set("category", category);
  const response = await workspaceRequest(
    "data/universes/definitions",
    "GET",
    undefined,
    query,
    90_000,
  );
  if (!response?.ok)
    return { items: [] as UniverseDefinition[], total: 0, available: false };
  const result = await response.json();
  return {
    items: result.items as UniverseDefinition[],
    total: result.total as number,
    available: true,
  };
}

export async function workspaceUniverseSnapshot(id: string) {
  if (!/^[a-f0-9-]{36}$/i.test(id)) return null;
  const response = await workspaceRequest(`data/universes/snapshots/${id}`);
  return response?.ok ? ((await response.json()) as UniverseSnapshot) : null;
}

export async function workspaceUniverseSnapshots() {
  const response = await workspaceRequest("data/universes/snapshots");
  return response?.ok ? ((await response.json()) as UniverseSnapshot[]) : [];
}

export async function createUniverseSnapshot(
  body: { universe_id: string } | { name: string; instrument_ids: string[] },
) {
  const response = await workspaceRequest(
    "data/universes/" + ("universe_id" in body ? "resolve" : "custom"),
    "POST",
    body,
    undefined,
    90_000,
  );
  if (!response?.ok)
    return { error: response?.status === 403 ? "permission" : "unavailable" };
  const snapshot = (await response.json()) as UniverseSnapshot;
  return { snapshot_id: snapshot.snapshot_id };
}
