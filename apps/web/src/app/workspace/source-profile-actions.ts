"use server";
import { workspaceRequest } from "../../features/auth/workspace-request";
import type { SourceProfileCatalog } from "../../features/data/source-profile-panel";

/** The API validates the shared contract and derives scope from this session. */
export async function readSourceProfiles(): Promise<{
  catalog?: SourceProfileCatalog;
  error?: string;
  missing?: string[];
}> {
  try {
    // Each owner exposes the same scoped contract. Do not hide one owner's
    // outage by presenting the other owner's entries as the complete catalog.
    const groups = [
      ["data/collector/source-profiles", "行情与研究采集说明"],
      ["data/universes/source-profiles", "当前成员说明"],
    ];
    const results = await Promise.all(
      groups.map(async ([path, name]) => {
        try {
          const response = await workspaceRequest(path);
          return {
            name,
            value: response?.ok
              ? ((await response.json()) as SourceProfileCatalog)
              : null,
          };
        } catch {
          return { name, value: null };
        }
      }),
    );
    const available = results.filter((r) => r.value);
    if (!available.length) return { error: "unavailable" };
    const scope = available[0].value!.scope_id;
    if (available.some((r) => r.value!.scope_id !== scope))
      return { error: "unavailable" };
    const profiles = available.flatMap((r) => r.value!.profiles);
    if (new Set(profiles.map((p) => p.source)).size !== profiles.length)
      return { error: "unavailable" };
    return {
      catalog: { scope_id: scope, profiles },
      missing: results.filter((r) => !r.value).map((r) => r.name),
    };
  } catch {
    return { error: "unavailable" };
  }
}
