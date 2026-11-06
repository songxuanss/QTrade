// @vitest-environment happy-dom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DataWorkbenchViews } from "../../src/features/data/data-workbench-views";
import type {
  CollectorJob,
  CollectorSource,
} from "../../src/features/data/collector-types";
import type { UniverseSnapshot } from "../../src/features/data/universe-server";
import type { CollectorPreview } from "../../src/features/data/collector-types";
import {
  previewCollector,
  submitCollector,
} from "../../src/app/workspace/collector-actions";

const actions = vi.hoisted(() => ({ readCollectorJob: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));
vi.mock("../../src/features/auth/workspace-request", () => ({
  workspaceRequest: vi.fn(),
}));
vi.mock("../../src/app/workspace/collection-replay-actions", () => ({
  listCollectionInputs: vi.fn(),
  previewCollectionReplay: vi.fn(),
  readCollectionReplay: vi.fn(),
  readCollectionReplayState: vi.fn(),
  submitCollectionReplay: vi.fn(),
}));
vi.mock("../../src/app/workspace/collector-actions", () => ({
  controlCollector: vi.fn(),
  previewCollector: vi.fn(),
  readCollectorDefaultRange: vi.fn(),
  readCollectorFactorCatalog: vi.fn(),
  readCollectorJob: actions.readCollectorJob,
  readCollectorProcessingFailures: vi.fn(),
  readCollectorQuality: vi.fn(),
  readCollectorRecords: vi.fn(),
  readCollectorUsage: vi.fn(),
  saveCollectorPreferences: vi.fn(),
  submitCollector: vi.fn(),
}));

const jobId = "33333333-3333-4333-8333-333333333333";

function makeSource(
  overrides: Partial<CollectorSource> &
    Pick<CollectorSource, "source" | "name">,
): CollectorSource {
  return {
    provider: "mairui",
    dataset_kind: "bars",
    periods: ["1d", "5m"],
    instrument_kinds: ["stock"],
    markets: ["CN"],
    timezone: "Asia/Shanghai",
    configured: true,
    evidence: "configured",
    limitations: [],
    ...overrides,
  };
}

const catalog: CollectorSource[] = [
  makeSource({
    source: "mairui.history.bars",
    name: "MaiRui 日线/分钟历史",
    periods: ["5m", "15m", "30m", "1h", "1d"],
    tier: "free",
  }),
  makeSource({
    source: "mairui.pro.bars",
    name: "MaiRui Pro 分钟历史",
    periods: ["1m", "5m", "15m", "30m", "1h"],
    tier: "paid",
  }),
];

function makeJob(state = "running"): CollectorJob {
  return {
    job_id: jobId,
    state,
    request: {
      schema_version: 1,
      scope_id: "scope",
      actor_id: "actor",
      correlation_id: `correlation-${jobId}`,
      request_id: `request-${jobId}`,
      source: "mairui.pro.bars",
      dataset_kind: "bars",
      instrument_ids: ["600519.SH"],
      granularity: "5m",
      start: "2026-01-01T00:00:00Z",
      end: "2026-01-02T00:00:00Z",
      fields: [],
      mode: "missing",
    },
    created_at: "2026-01-01T00:00:00Z",
    counts: { running: 1 },
    total_parts: 1,
    offset: 0,
    next_offset: null,
    instrument_names: { "600519.SH": "贵州茅台" },
    field_definitions: [],
    parts: [],
  };
}

/** The four-view assembly must keep one collector instance across the
 * download and jobs views: URL compatibility, draft retention and job
 * selection all hinge on that single-instance contract. */
describe("DataWorkbenchViews four-view assembly", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    // Deep links carrying collector_job trigger the panel's initial job read;
    // a resolved default keeps those effects from exploding mid-render.
    actions.readCollectorJob.mockReset();
    actions.readCollectorJob.mockResolvedValue({ job: makeJob("running") });
    if (!Element.prototype.scrollIntoView) {
      (Element.prototype as unknown as Record<string, unknown>).scrollIntoView =
        () => {};
    }
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  function visibleView(): string | null {
    return (
      [...container.querySelectorAll<HTMLElement>("[data-workbench-view]")]
        .find((panel) => !panel.hidden)
        ?.getAttribute("data-workbench-view") ?? null
    );
  }

  async function renderViews(
    search = "",
    overrides: Record<string, unknown> = {},
  ) {
    window.history.replaceState(null, "", `/workspace/data${search}`);
    await act(async () => {
      root.render(
        <DataWorkbenchViews
          collectorProps={{
            catalog,
            factors: [],
            factorsAvailable: false,
            initialJobs: [makeJob("queued")],
            available: true,
            canManage: true,
            snapshots: [],
            ...overrides,
          }}
          universePanel={<div data-slot="universe" />}
          entriesTabs={<div data-slot="entries" />}
          assetsContent={<div data-slot="assets" />}
          sourcesTabs={<div data-slot="sources" />}
          initialView="download"
        />,
      );
    });
  }

  async function clickTab(title: string) {
    const tab = [
      ...container.querySelectorAll<HTMLButtonElement>("button"),
    ].find(
      (button) =>
        button.getAttribute("role") === "tab" &&
        button.textContent?.trim() === title,
    )!;
    await act(async () => tab.click());
  }

  it("maps every legacy data_view value onto the new four views", async () => {
    const cases: [string, string][] = [
      ["?data_view=pools", "catalog"],
      ["?data_view=collect", "download"],
      ["?data_view=collect&collect_view=results", "jobs"],
      [`?data_view=collect&collector_job=${jobId}`, "jobs"],
      ["?data_view=fundamentals", "download"],
      ["?data_view=ticks", "download"],
      ["?data_view=assets", "jobs"],
      ["?data_view=monitor", "sources"],
      ["?data_view=schedules", "sources"],
      ["?data_view=jobs", "jobs"],
      ["?data_view=catalog", "catalog"],
    ];
    for (const [search, expected] of cases) {
      await act(async () => root.unmount());
      container.remove();
      container = document.createElement("div");
      document.body.append(container);
      root = createRoot(container);
      await renderViews(search);
      expect(`${search} -> ${visibleView()}`).toBe(`${search} -> ${expected}`);
    }
  });

  it("keeps fragment anchors resolving to the owning view", async () => {
    await renderViews("#batches");
    expect(visibleView()).toBe("jobs");
  });

  it("keeps the create draft mounted while switching to jobs and back", async () => {
    await renderViews();
    expect(visibleView()).toBe("download");
    const instruments = container.querySelector<HTMLInputElement>(
      'input[aria-label="独立采集标的"]',
    )!;
    const setValue = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!;
    await act(async () => {
      setValue.call(instruments, "601288.SH");
      instruments.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await clickTab("任务与结果");
    expect(visibleView()).toBe("jobs");
    // Both halves stay in the DOM; only visibility moves with the URL.
    expect(container.querySelector(".collector-form")).not.toBeNull();
    await clickTab("多维下载");
    expect(visibleView()).toBe("download");
    expect(
      container.querySelector<HTMLInputElement>(
        'input[aria-label="独立采集标的"]',
      )?.value,
    ).toBe("601288.SH");
  });

  it("selecting a saved task moves the URL to the jobs view and loads it", async () => {
    actions.readCollectorJob.mockResolvedValue({ job: makeJob("running") });
    await renderViews();
    const recent =
      container.querySelector<HTMLDetailsElement>(".collector-recent")!;
    recent.open = true;
    const jobButton = [
      ...recent.querySelectorAll<HTMLButtonElement>("button"),
    ][0];
    await act(async () => jobButton.click());
    await act(async () => {});
    const query = new URL(window.location.href).searchParams;
    expect(query.get("data_view")).toBe("jobs");
    expect(query.get("collector_job")).toBe(jobId);
    expect(visibleView()).toBe("jobs");
    expect(
      container
        .querySelector(".collector-run")
        ?.getAttribute("data-collector-job"),
    ).toBe(jobId);
  });

  it("exposes the independent-collector anchor target in split mode", async () => {
    await renderViews();
    expect(container.querySelector("#independent-collector")).not.toBeNull();
    expect(container.querySelector("#collector-title")?.textContent).toBe(
      "独立数据采集",
    );
  });

  it("shows exactly one error channel for the active view", async () => {
    await renderViews();
    // Trigger a create-side failure: preview without any scope.
    const form = container.querySelector<HTMLFormElement>(
      "form.collector-form",
    )!;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
    });
    await act(async () => {});
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(
      container.querySelector('.collector-footer [role="alert"]')?.textContent,
    ).toContain("请检查标的、日期范围和字段选择");
    await clickTab("任务与结果");
    // The create-side inline alert stays owned by the create half; the results
    // banner does not duplicate it while the error belongs to a create action.
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(1);
  });

  const poolSnapshot: UniverseSnapshot = {
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
  };

  async function typeValue(input: HTMLInputElement, value: string) {
    const setValue = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!;
    await act(async () => {
      setValue.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }

  function findButton(text: string, scope: ParentNode = container) {
    return [...scope.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent?.trim() === text,
    )!;
  }

  it("summarizes the draft in the scope bar and marks inherited pools", async () => {
    await renderViews("", { snapshots: [poolSnapshot] });
    const bar = container.querySelector<HTMLElement>(
      "[data-workbench-scope-bar]",
    )!;
    expect(bar.textContent).toContain("尚未选择标的");
    const instruments = container.querySelector<HTMLInputElement>(
      'input[aria-label="独立采集标的"]',
    )!;
    await typeValue(instruments, "601288.SH, 600519.SH");
    expect(bar.textContent).toContain("2 个手工标的");
    const pool = container.querySelector<HTMLInputElement>(
      ".collector-pool-options input",
    )!;
    await act(async () => pool.click());
    expect(bar.querySelector('[data-scope-inherited="true"]')).not.toBeNull();
    expect(bar.textContent).toContain("1 个股票池（2 个成员）");
    expect(bar.textContent).toContain("去重后 3 个标的");
    const start = container.querySelector<HTMLInputElement>(
      'input[aria-label="独立采集开始日期"]',
    )!;
    const end = container.querySelector<HTMLInputElement>(
      'input[aria-label="独立采集结束日期"]',
    )!;
    await typeValue(start, "2026-01-01");
    await typeValue(end, "2026-12-31");
    expect(bar.textContent).toContain("2026-01-01 → 2026-12-31");
  });

  it("jumps from the scope bar back to the download view", async () => {
    await renderViews();
    await clickTab("任务与结果");
    expect(visibleView()).toBe("jobs");
    const target = container.querySelector<HTMLButtonElement>(
      '[data-scope-segment="target"]',
    )!;
    await act(async () => target.click());
    expect(visibleView()).toBe("download");
    const bar = container.querySelector<HTMLElement>(
      "[data-workbench-scope-bar]",
    )!;
    await act(async () => findButton("数据目录", bar).click());
    expect(visibleView()).toBe("catalog");
  });

  it("detaches inherited pools without touching manual codes", async () => {
    await renderViews("", { snapshots: [poolSnapshot] });
    const instruments = container.querySelector<HTMLInputElement>(
      'input[aria-label="独立采集标的"]',
    )!;
    await typeValue(instruments, "600519.SH");
    const pool = container.querySelector<HTMLInputElement>(
      ".collector-pool-options input",
    )!;
    await act(async () => pool.click());
    await act(async () => findButton("解除继承").click());
    expect(pool.checked).toBe(false);
    expect(instruments.value).toBe("600519.SH");
    expect(container.querySelector("[aria-label='已继承股票池']")).toBeNull();
  });

  it("adds drawer picks to the draft and restores focus", async () => {
    await renderViews("", { snapshots: [poolSnapshot] });
    const trigger = findButton("从清单选择");
    await act(async () => trigger.click());
    const dialog = container.querySelector<HTMLElement>(
      "[data-instrument-drawer]",
    )!;
    expect(dialog.getAttribute("role")).toBe("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    const search = dialog.querySelector<HTMLInputElement>(
      'input[aria-label="搜索标的清单"]',
    )!;
    await typeValue(search, "600036");
    const member = dialog.querySelector<HTMLInputElement>(
      '[data-drawer-member="600036.SH"] input',
    )!;
    await act(async () => member.click());
    await act(async () => findButton("加入本次范围", dialog).click());
    expect(container.querySelector("[data-instrument-drawer]")).toBeNull();
    expect(
      container.querySelector<HTMLInputElement>(
        'input[aria-label="独立采集标的"]',
      )!.value,
    ).toBe("600036.SH");
    await vi.waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("closes the drawer with Escape", async () => {
    await renderViews("", { snapshots: [poolSnapshot] });
    const trigger = findButton("从清单选择");
    await act(async () => trigger.click());
    expect(container.querySelector("[data-instrument-drawer]")).not.toBeNull();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(container.querySelector("[data-instrument-drawer]")).toBeNull();
  });

  function makePreview(): CollectorPreview {
    return {
      request: {
        schema_version: 1,
        scope_id: "scope",
        actor_id: "actor",
        correlation_id: "correlation-preview",
        request_id: "request-preview",
        source: "mairui.history.bars",
        dataset_kind: "bars",
        instrument_ids: ["601288.SH"],
        granularity: "1d",
        start: "2025-01-01T00:00:00Z",
        end: "2025-01-02T00:00:00Z",
        fields: [],
        mode: "missing",
      },
      mapping_version: "m1",
      coverage_version: "coverage-v1",
      planned_parts: 1,
      completed_parts: 0,
      active_parts: 0,
      missing_parts: 1,
      instrument_names: { "601288.SH": "农业银行" },
      field_definitions: [],
      limitations: [],
    };
  }

  async function fillDraft(instruments: string) {
    await typeValue(
      container.querySelector<HTMLInputElement>(
        'input[aria-label="独立采集标的"]',
      )!,
      instruments,
    );
    await typeValue(
      container.querySelector<HTMLInputElement>(
        'input[aria-label="独立采集开始日期"]',
      )!,
      "2025-01-01",
    );
    await typeValue(
      container.querySelector<HTMLInputElement>(
        'input[aria-label="独立采集结束日期"]',
      )!,
      "2025-12-31",
    );
  }

  async function flush() {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
  }

  it("adds the current draft as an orchestration card", async () => {
    await renderViews();
    await fillDraft("601288.SH");
    await act(async () => findButton("加入编排").click());
    const card = container.querySelector("[data-orchestration-card]")!;
    expect(card.getAttribute("data-card-state")).toBe("draft");
    expect(card.textContent).toContain("601288.SH");
    expect(card.textContent).toContain("2025-01-01 → 2025-12-31");
  });

  it("previews and submits orchestration cards in order", async () => {
    vi.mocked(previewCollector).mockResolvedValue({ preview: makePreview() });
    vi.mocked(submitCollector).mockResolvedValue({ job: makeJob("queued") });
    await renderViews();
    await fillDraft("601288.SH");
    await act(async () => findButton("加入编排").click());
    await fillDraft("600519.SH");
    await act(async () => findButton("加入编排").click());
    expect(
      container.querySelectorAll("[data-orchestration-card]"),
    ).toHaveLength(2);
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>(
          '[data-orchestration-action="preview"]',
        )!
        .click(),
    );
    await flush();
    const cards = container.querySelectorAll("[data-orchestration-card]");
    expect(
      [...cards].map((card) => card.getAttribute("data-card-state")),
    ).toEqual(["previewed", "previewed"]);
    expect(vi.mocked(previewCollector)).toHaveBeenCalledTimes(2);
    expect(
      vi
        .mocked(previewCollector)
        .mock.calls.map((call) => call[0].instrument_ids),
    ).toEqual([["601288.SH"], ["600519.SH"]]);
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>(
          '[data-orchestration-action="submit"]',
        )!
        .click(),
    );
    await flush();
    expect(vi.mocked(submitCollector)).toHaveBeenCalledTimes(2);
    expect(
      [...container.querySelectorAll("[data-orchestration-card]")].map((card) =>
        card.getAttribute("data-card-state"),
      ),
    ).toEqual(["submitted", "submitted"]);
    expect(visibleView()).toBe("jobs");
  });

  it("stops sequential submission at the first failing card", async () => {
    vi.mocked(previewCollector).mockResolvedValue({ preview: makePreview() });
    vi.mocked(submitCollector)
      .mockResolvedValueOnce({ job: makeJob("queued") })
      .mockResolvedValueOnce({ error: "unavailable" });
    await renderViews();
    await fillDraft("601288.SH");
    await act(async () => findButton("加入编排").click());
    await fillDraft("600519.SH");
    await act(async () => findButton("加入编排").click());
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>(
          '[data-orchestration-action="preview"]',
        )!
        .click(),
    );
    await flush();
    await act(async () =>
      container
        .querySelector<HTMLButtonElement>(
          '[data-orchestration-action="submit"]',
        )!
        .click(),
    );
    await flush();
    expect(vi.mocked(submitCollector)).toHaveBeenCalledTimes(2);
    expect(
      [...container.querySelectorAll("[data-orchestration-card]")].map((card) =>
        card.getAttribute("data-card-state"),
      ),
    ).toEqual(["submitted", "failed"]);
    expect(
      container.querySelector("[data-orchestration] [role='alert']")
        ?.textContent,
    ).toContain("顺序提交在该卡片停止");
  });

  it("mirrors the draft count in the mobile selection bar", async () => {
    await renderViews();
    expect(container.querySelector("[data-mobile-scope-bar]")).toBeNull();
    const instruments = container.querySelector<HTMLInputElement>(
      'input[aria-label="独立采集标的"]',
    )!;
    await typeValue(instruments, "601288.SH, 600519.SH");
    const bar = container.querySelector<HTMLElement>(
      "[data-mobile-scope-bar]",
    )!;
    expect(bar.textContent).toContain("已选 2 个标的");
    await act(async () => findButton("查看清单", bar).click());
    expect(container.querySelector("[data-instrument-drawer]")).not.toBeNull();
  });
});
