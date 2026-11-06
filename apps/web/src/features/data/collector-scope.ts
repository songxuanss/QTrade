import type { UniverseSnapshot } from "./universe-server";

/** Draft scope shared by the global scope bar and the mobile selection bar.
 * Only presentation data lives here; collection semantics stay in the panel. */
export type CollectorScope = {
  kind: "bars" | "factors";
  period: string;
  sourceName: string;
  sourceConfigured: boolean;
  first: string;
  last: string;
  manualIds: readonly string[];
  snapshots: readonly UniverseSnapshot[];
  snapshotIds: readonly string[];
  selectedFields: readonly string[];
  factorNames: ReadonlyMap<string, string>;
};

export type CollectorScopeSummary = {
  kindLabel: string;
  pools: { count: number; names: string[]; members: number };
  manual: { count: number };
  /** Union of pool members and manual codes, deduplicated the same way the
   * submit path deduplicates. */
  combined: number;
  inherited: boolean;
  dates: { first: string; last: string; label: string; set: boolean };
  source: { label: string; configured: boolean };
  fields: { count: number; names: string[] };
};

const separators = /[\s,，;；]+/;

/** Codes a person typed, in a stable order, without duplicates. */
export function parseInstrumentIds(value: string): string[] {
  const seen = new Set<string>();
  for (const part of value.split(separators)) {
    const id = part.trim();
    if (id) seen.add(id);
  }
  return [...seen];
}

/** Append drawer picks to the draft without reordering existing input. */
export function mergeInstrumentIds(
  value: string,
  added: readonly string[],
): string {
  const merged = parseInstrumentIds(value);
  for (const raw of added) {
    const id = raw.trim();
    if (id && !merged.includes(id)) merged.push(id);
  }
  return merged.join(", ");
}

/** Remove codes from the draft (used when a pick is unchecked in the drawer). */
export function removeInstrumentIds(
  value: string,
  removed: readonly string[],
): string {
  const drop = new Set(removed.map((id) => id.trim()));
  return parseInstrumentIds(value)
    .filter((id) => !drop.has(id))
    .join(", ");
}

export function scopeSummary(scope: CollectorScope): CollectorScopeSummary {
  const selected = scope.snapshots.filter((item) =>
    scope.snapshotIds.includes(item.snapshot_id),
  );
  const poolMembers = new Set<string>();
  for (const pool of selected)
    for (const member of pool.members)
      poolMembers.add(member.instrument.instrument_id);
  const members = new Set(poolMembers);
  for (const id of scope.manualIds) members.add(id);
  const names = selected.map((item) => item.name);
  const factorNames = scope.selectedFields.map(
    (id) => scope.factorNames.get(id) ?? id,
  );
  const kindLabel = scope.kind === "factors" ? "量化因子" : "价格行情";
  const datesSet = Boolean(scope.first && scope.last);
  return {
    kindLabel,
    pools: { count: selected.length, names, members: poolMembers.size },
    manual: { count: scope.manualIds.length },
    combined: members.size,
    inherited: selected.length > 0,
    dates: {
      first: scope.first,
      last: scope.last,
      label: datesSet ? `${scope.first} → ${scope.last}` : "未设置",
      set: datesSet,
    },
    source: {
      label: scope.sourceName || "未选择来源",
      configured: scope.sourceConfigured,
    },
    fields: { count: scope.selectedFields.length, names: factorNames },
  };
}

/** One-line target description used by the scope bar and the mobile bar. */
export function targetLabel(summary: CollectorScopeSummary): string {
  const parts: string[] = [];
  if (summary.pools.count)
    parts.push(
      `${summary.pools.count} 个股票池（${summary.pools.members} 个成员）`,
    );
  if (summary.manual.count) parts.push(`${summary.manual.count} 个手工标的`);
  if (!parts.length) return "尚未选择标的";
  return `${parts.join(" · ")} · 去重后 ${summary.combined} 个标的`;
}
