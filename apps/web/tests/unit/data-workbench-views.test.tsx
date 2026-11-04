// @vitest-environment happy-dom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DataWorkbenchViews } from "../../src/features/data/data-workbench-views";
import type {
  CollectorJob,
  CollectorSource,
} from "../../src/features/data/collector-types";

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

  async function renderViews(search = "") {
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
});
