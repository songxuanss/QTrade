import type {
  CollectorDimensionDraft,
  CollectorPreview,
} from "./collector-types";

/** A composition card is a draft of one collection request. Preview and submit
 * still produce and freeze ordinary collection requests; cards carry only the
 * dimensions a person chooses to run in sequence. */
export type OrchestrationState =
  "draft" | "previewing" | "previewed" | "submitting" | "submitted" | "failed";

export type OrchestrationCard = {
  dimension_id: string;
  kind: "bars" | "factors";
  source: string;
  period: string;
  first: string;
  last: string;
  instruments: string;
  snapshotIds: string[];
  selectedFields: string[];
  mode: "missing" | "all";
  state: OrchestrationState;
  preview?: CollectorPreview;
  jobId?: string;
  error?: string;
};

export const MAX_CARDS = 20;

export function newDimensionId(): string {
  const crypto = globalThis.crypto;
  if (crypto && typeof crypto.randomUUID === "function")
    return crypto.randomUUID();
  return `dim-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 10)}`;
}

export function toDimensionDraft(
  card: OrchestrationCard,
): CollectorDimensionDraft {
  return {
    dimension_id: card.dimension_id,
    dataset_kind: card.kind,
    source: card.source,
    granularity: card.kind === "factors" ? "1d" : card.period,
    first: card.first || null,
    last: card.last || null,
    instruments: card.instruments,
    snapshot_ids: card.snapshotIds,
    fields: card.selectedFields,
    mode: card.mode,
  };
}

export function fromDimensionDraft(
  draft: CollectorDimensionDraft,
): OrchestrationCard {
  return {
    dimension_id: draft.dimension_id,
    kind: draft.dataset_kind,
    source: draft.source,
    period: draft.granularity,
    first: draft.first ?? "",
    last: draft.last ?? "",
    instruments: draft.instruments,
    snapshotIds: [...draft.snapshot_ids],
    selectedFields: [...draft.fields],
    mode: draft.mode,
    state: "draft",
  };
}

const kindLabel: Record<OrchestrationCard["kind"], string> = {
  bars: "价格行情",
  factors: "量化因子",
};

/** Compact card header: kind/period, source and the scoped targets. */
export function cardLabel(
  card: OrchestrationCard,
  poolNames: ReadonlyMap<string, string>,
): string {
  const parts = [
    `${kindLabel[card.kind]} · ${card.kind === "factors" ? "1d" : card.period}`,
  ];
  if (card.instruments.trim()) parts.push(card.instruments.trim());
  const pools = card.snapshotIds
    .map((id) => poolNames.get(id))
    .filter((name): name is string => Boolean(name));
  if (pools.length) parts.push(`股票池 ${pools.join("、")}`);
  if (card.first && card.last) parts.push(`${card.first} → ${card.last}`);
  if (card.kind === "factors" && card.selectedFields.length)
    parts.push(`${card.selectedFields.length} 个因子`);
  return parts.join(" · ");
}

export function cardTargets(card: OrchestrationCard): string {
  const parts: string[] = [];
  const manual = card.instruments
    .split(/[\s,，;；]+/)
    .map((item) => item.trim())
    .filter(Boolean).length;
  if (card.snapshotIds.length)
    parts.push(`${card.snapshotIds.length} 个股票池`);
  if (manual) parts.push(`${manual} 个手工标的`);
  return parts.join(" · ") || "范围待设置";
}

export const stateLabel: Record<OrchestrationState, string> = {
  draft: "待预览",
  previewing: "预览中",
  previewed: "可提交",
  submitting: "提交中",
  submitted: "已提交",
  failed: "失败",
};
