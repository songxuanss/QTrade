import { describe, expect, it } from "vitest";
import {
  mergeInstrumentIds,
  parseInstrumentIds,
  removeInstrumentIds,
  scopeSummary,
  targetLabel,
  type CollectorScope,
} from "../../src/features/data/collector-scope";
import type { UniverseSnapshot } from "../../src/features/data/universe-server";

const snapshots: UniverseSnapshot[] = [
  {
    snapshot_id: "snap-bank",
    universe_id: "u1",
    version: 2,
    name: "银行",
    observed_at: "2026-01-02T00:00:00Z",
    market_scope: ["CN"],
    content_sha256: "sha",
    members: [
      {
        instrument: {
          instrument_id: "601288.SH",
          name: "农业银行",
          exchange: "SH",
        },
        affiliations: [],
      },
      {
        instrument: {
          instrument_id: "600036.SH",
          name: "招商银行",
          exchange: "SH",
        },
        affiliations: [],
      },
    ],
    reference_issues: [],
  },
];

function scope(overrides: Partial<CollectorScope> = {}): CollectorScope {
  return {
    kind: "bars",
    period: "1d",
    sourceName: "MaiRui 日线/分钟历史",
    sourceConfigured: true,
    first: "",
    last: "",
    manualIds: [],
    snapshots: [],
    snapshotIds: [],
    selectedFields: [],
    factorNames: new Map(),
    ...overrides,
  };
}

describe("collector scope helpers", () => {
  it("parses manual codes across every accepted separator without duplicates", () => {
    expect(
      parseInstrumentIds(" 601288.SH，600036.SH;601288.SH  index:000001.SH\n"),
    ).toEqual(["601288.SH", "600036.SH", "index:000001.SH"]);
    expect(parseInstrumentIds("   ")).toEqual([]);
  });

  it("merges drawer picks after the existing draft and skips duplicates", () => {
    expect(
      mergeInstrumentIds("601288.SH", ["600036.SH", "601288.SH", " "]),
    ).toBe("601288.SH, 600036.SH");
    expect(mergeInstrumentIds("", ["601288.SH"])).toBe("601288.SH");
  });

  it("removes only the requested codes", () => {
    expect(removeInstrumentIds("601288.SH, 600036.SH", ["600036.SH"])).toBe(
      "601288.SH",
    );
    expect(removeInstrumentIds("601288.SH", ["600036.SH"])).toBe("601288.SH");
  });

  it("counts the union of inherited pool members and manual codes", () => {
    const summary = scopeSummary(
      scope({
        manualIds: ["601288.SH", "600519.SH"],
        snapshots,
        snapshotIds: ["snap-bank"],
      }),
    );
    expect(summary.pools).toEqual({
      count: 1,
      names: ["银行"],
      members: 2,
    });
    expect(summary.manual.count).toBe(2);
    // 601288.SH is both manual and a pool member; it counts once.
    expect(summary.combined).toBe(3);
    expect(summary.inherited).toBe(true);
    expect(targetLabel(summary)).toBe(
      "1 个股票池（2 个成员） · 2 个手工标的 · 去重后 3 个标的",
    );
  });

  it("labels an empty scope, dates and source configuration explicitly", () => {
    const summary = scopeSummary(scope({ sourceConfigured: false }));
    expect(targetLabel(summary)).toBe("尚未选择标的");
    expect(summary.dates.label).toBe("未设置");
    expect(summary.dates.set).toBe(false);
    expect(summary.source.configured).toBe(false);
    expect(summary.source.label).toBe("MaiRui 日线/分钟历史");
    expect(summary.fields.count).toBe(0);
  });

  it("reports dates and factor names for factor drafts", () => {
    const summary = scopeSummary(
      scope({
        kind: "factors",
        period: "1d",
        first: "2025-01-01",
        last: "2025-12-31",
        selectedFields: ["pe_ttm", "unknown"],
        factorNames: new Map([["pe_ttm", "市盈率 TTM"]]),
      }),
    );
    expect(summary.kindLabel).toBe("量化因子");
    expect(summary.dates.label).toBe("2025-01-01 → 2025-12-31");
    expect(summary.fields).toEqual({
      count: 2,
      names: ["市盈率 TTM", "unknown"],
    });
  });
});
