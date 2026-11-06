import { describe, expect, it } from "vitest";
import {
  cardLabel,
  cardTargets,
  fromDimensionDraft,
  newDimensionId,
  toDimensionDraft,
  type OrchestrationCard,
} from "../../src/features/data/collection-orchestration";
import type { CollectorDimensionDraft } from "../../src/features/data/collector-types";

const card: OrchestrationCard = {
  dimension_id: "11111111-1111-4111-8111-111111111111",
  kind: "bars",
  source: "mairui.history.bars",
  period: "5m",
  first: "2025-01-01",
  last: "2025-01-31",
  instruments: "601288.SH, 600519.SH",
  snapshotIds: ["snap-bank"],
  selectedFields: [],
  mode: "missing",
  state: "draft",
};

const poolNames = new Map([["snap-bank", "银行"]]);

describe("collection orchestration helpers", () => {
  it("round-trips a card through the persisted dimension draft", () => {
    const draft = toDimensionDraft(card);
    expect(draft).toEqual<CollectorDimensionDraft>({
      dimension_id: card.dimension_id,
      dataset_kind: "bars",
      source: "mairui.history.bars",
      granularity: "5m",
      first: "2025-01-01",
      last: "2025-01-31",
      instruments: "601288.SH, 600519.SH",
      snapshot_ids: ["snap-bank"],
      fields: [],
      mode: "missing",
    });
    expect(fromDimensionDraft(draft)).toEqual(card);
  });

  it("normalizes empty dates and factor periods when persisting", () => {
    const draft = toDimensionDraft({
      ...card,
      kind: "factors",
      period: "5m",
      first: "",
      last: "",
      selectedFields: ["pe_ttm"],
    });
    expect(draft.first).toBeNull();
    expect(draft.last).toBeNull();
    expect(draft.granularity).toBe("1d");
    const restored = fromDimensionDraft(draft);
    expect(restored.first).toBe("");
    expect(restored.last).toBe("");
    expect(restored.period).toBe("1d");
    expect(restored.state).toBe("draft");
  });

  it("labels cards with kind, period, targets, dates and pools", () => {
    expect(cardLabel(card, poolNames)).toBe(
      "价格行情 · 5m · 601288.SH, 600519.SH · 股票池 银行 · 2025-01-01 → 2025-01-31",
    );
    expect(
      cardLabel(
        { ...card, kind: "factors", selectedFields: ["pe_ttm"] },
        poolNames,
      ),
    ).toContain("量化因子 · 1d");
    expect(
      cardLabel(
        { ...card, kind: "factors", selectedFields: ["pe_ttm"] },
        poolNames,
      ),
    ).toContain("1 个因子");
    expect(cardTargets(card)).toBe("1 个股票池 · 2 个手工标的");
    expect(cardTargets({ ...card, instruments: "", snapshotIds: [] })).toBe(
      "范围待设置",
    );
  });

  it("issues unique dimension identifiers", () => {
    const ids = new Set(Array.from({ length: 20 }, () => newDimensionId()));
    expect(ids.size).toBe(20);
  });
});
