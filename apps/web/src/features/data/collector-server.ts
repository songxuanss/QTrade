import "server-only";
import { workspaceRequest } from "../auth/workspace-request";
import type {
  CollectorSource,
  FactorCatalogSnapshot,
  CollectorJob,
  CollectorPreferences,
} from "./collector-types";

async function readSnapshot(path: string, timeout = 10_000) {
  const response = await workspaceRequest(
    path,
    "GET",
    undefined,
    undefined,
    timeout,
  );
  if (!response?.ok) return { ok: false, value: null };
  try {
    // Consume each body immediately: waiting for another slow source first can
    // let this response's abort deadline expire after its headers arrived.
    return { ok: true, value: await response.json() };
  } catch {
    return { ok: false, value: null };
  }
}

export function isFactorCatalog(
  value: unknown,
): value is FactorCatalogSnapshot {
  if (!value || typeof value !== "object") return false;
  const v = value as Partial<FactorCatalogSnapshot>;
  return (
    v.schema_version === 1 &&
    v.source === "mairui.factor.history" &&
    ["fresh", "stale", "loading", "unavailable", "unconfigured"].includes(
      v.status ?? "",
    ) &&
    Array.isArray(v.definitions) &&
    v.definitions.every(
      (d) => d && typeof d.factor_id === "string" && typeof d.name === "string",
    )
  );
}

export async function workspaceCollector() {
  const [catalog, factors, jobs, preferences] = await Promise.all([
    readSnapshot("data/collector/catalog"),
    readSnapshot("data/collector/factor-catalog"),
    readSnapshot("data/collector/jobs"),
    readSnapshot("data/collector/preferences"),
  ]);
  const factorCatalog =
    factors.ok && isFactorCatalog(factors.value) ? factors.value : null;
  return {
    factorCatalog,
    available: Boolean(catalog.ok && Array.isArray(catalog.value)),
    catalog: (Array.isArray(catalog.value)
      ? catalog.value
      : []) as CollectorSource[],
    factors: factorCatalog?.definitions ?? [],
    factorsAvailable: Boolean(factorCatalog?.definitions.length),
    jobs: (Array.isArray(jobs.value) ? jobs.value : []) as CollectorJob[],
    initialPreferences: (preferences?.ok
      ? preferences.value
      : null) as CollectorPreferences | null,
    preferencesAvailable: Boolean(preferences?.ok),
  };
}
export async function workspaceInstrumentNames(
  ids: string[] = [],
): Promise<Record<string, string>> {
  const query = new URLSearchParams();
  for (const id of [...new Set(ids)].slice(0, 200))
    query.append("instrument_id", id);
  const response = await workspaceRequest(
    "data/collector/names",
    "GET",
    undefined,
    query,
    30_000,
  );
  return response?.ok ? await response.json() : {};
}

export async function workspaceResearchCollection(
  kind = "financial_statement",
) {
  const [catalog, jobs] = await Promise.all([
    readSnapshot("data/collector/research/catalog"),
    readSnapshot("data/collector/research/jobs"),
  ]);
  const sources = (
    Array.isArray(catalog.value) ? catalog.value : []
  ) as import("./research-collection-types").ResearchCapability[];
  const runs = (
    Array.isArray(jobs.value) ? jobs.value : []
  ) as import("./research-collection-types").ResearchJob[];
  return {
    available: Boolean(
      catalog.ok &&
      jobs.ok &&
      Array.isArray(catalog.value) &&
      Array.isArray(jobs.value),
    ),
    catalog: sources.filter((c) => c.kind === kind),
    jobs: runs.filter((j) => j.request.kind === kind),
  };
}
