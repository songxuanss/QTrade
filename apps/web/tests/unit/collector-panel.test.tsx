// @vitest-environment happy-dom

import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CollectorPanel } from "../../src/features/data/collector-panel";
import type { CollectorJob } from "../../src/features/data/collector-types";

const actions = vi.hoisted(() => ({ readCollectorJob: vi.fn() }));
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

const firstId = "11111111-1111-4111-8111-111111111111";
const secondId = "22222222-2222-4222-8222-222222222222";

function makeJob(
  id: string,
  state = "queued",
  name = "贵州茅台",
): CollectorJob {
  return {
    job_id: id,
    state,
    request: {
      schema_version: 1,
      scope_id: "scope",
      actor_id: "actor",
      correlation_id: `correlation-${id}`,
      request_id: `request-${id}`,
      source: "mairui.pro.bars",
      dataset_kind: "bars",
      instrument_ids: ["600519.SH"],
      granularity: "1d",
      start: "2026-01-01T00:00:00Z",
      end: "2026-01-02T00:00:00Z",
      fields: [],
      mode: "missing",
    },
    created_at: "2026-01-01T00:00:00Z",
    counts: { queued: state === "queued" ? 1 : 0 },
    total_parts: 1,
    offset: 0,
    next_offset: null,
    instrument_names: { "600519.SH": name },
    field_definitions: [],
    parts: [],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("CollectorPanel job observability", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    actions.readCollectorJob.mockReset();
    window.history.replaceState(
      null,
      "",
      "/workspace/data?collect_view=results",
    );
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  async function renderPanel(
    initialJobs: CollectorJob[],
    snapshots: ComponentProps<typeof CollectorPanel>["snapshots"] = [],
  ) {
    await act(async () => {
      root.render(
        <CollectorPanel
          catalog={[]}
          factors={[]}
          factorsAvailable={false}
          initialJobs={initialJobs}
          available
          canManage
          snapshots={snapshots}
        />,
      );
    });
  }

  async function selectJob(id: string) {
    const select = container.querySelector<HTMLSelectElement>(
      'select[aria-label="查看已保存采集任务"]',
    )!;
    await act(async () => {
      select.value = id;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  async function refreshJob() {
    const button = Array.from(container.querySelectorAll("button")).find(
      (item) => item.textContent === "刷新采集进度",
    )!;
    await act(async () => button.click());
  }

  it("polls queued jobs serially, backs off failures and synchronizes the selector", async () => {
    vi.useFakeTimers();
    actions.readCollectorJob
      .mockResolvedValueOnce({ job: makeJob(firstId, "queued", "最新快照") })
      .mockResolvedValueOnce({ error: "unavailable" })
      .mockResolvedValueOnce({ error: "unavailable" })
      .mockResolvedValueOnce({ job: makeJob(firstId, "running", "运行快照") });
    window.history.replaceState(
      null,
      "",
      `/workspace/data?collect_view=results&collector_job=${firstId}`,
    );
    await renderPanel([makeJob(firstId, "queued", "旧名称")]);

    expect(actions.readCollectorJob).toHaveBeenCalledTimes(1);
    expect(
      container.querySelector(`option[value="${firstId}"]`)?.textContent,
    ).toContain("最新快照");
    expect(
      container.querySelector(".collector-run [role=status]")?.textContent,
    ).toContain("自动刷新已开启");

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(actions.readCollectorJob).toHaveBeenCalledTimes(2);
    expect(container.querySelector(".collector-run")).not.toBeNull();
    expect(
      container.querySelector(".collector-run [role=alert]")?.textContent,
    ).toContain("当前显示上次成功快照");
    expect(container.querySelector(".collector-run")?.textContent).toContain(
      "最新快照",
    );

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(actions.readCollectorJob).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(actions.readCollectorJob).toHaveBeenCalledTimes(3);

    await act(async () => vi.advanceTimersByTimeAsync(6000));
    expect(actions.readCollectorJob).toHaveBeenCalledTimes(3);
    await act(async () => vi.advanceTimersByTimeAsync(6000));
    expect(actions.readCollectorJob).toHaveBeenCalledTimes(4);
    expect(
      container.querySelector(`option[value="${firstId}"]`)?.textContent,
    ).toContain("采集中");
    expect(
      container.querySelector(`option[value="${firstId}"]`)?.textContent,
    ).toContain("运行快照");
    expect(container.querySelector(".collector-run [role=alert]")).toBeNull();
  });

  it("keeps the current snapshot after a failed manual read and can retry", async () => {
    actions.readCollectorJob
      .mockResolvedValueOnce({ job: makeJob(firstId, "queued", "保留快照") })
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({
        job: makeJob(firstId, "completed", "完成快照"),
      });
    window.history.replaceState(
      null,
      "",
      `/workspace/data?collect_view=results&collector_job=${firstId}`,
    );
    await renderPanel([makeJob(firstId)]);

    await refreshJob();
    expect(
      container
        .querySelector(".collector-run")
        ?.getAttribute("data-collector-job"),
    ).toBe(firstId);
    expect(container.querySelector(".collector-run")?.textContent).toContain(
      "保留快照",
    );
    expect(
      container.querySelector(".collector-run [role=alert]")?.textContent,
    ).toContain("当前显示上次成功快照");

    await refreshJob();
    expect(container.querySelector(".collector-run")?.textContent).toContain(
      "完成快照",
    );
    expect(
      container.querySelector(`option[value="${firstId}"]`)?.textContent,
    ).toContain("采集完成");
    expect(container.querySelector(".collector-run [role=alert]")).toBeNull();
  });

  it("states which range takes effect before a plan is previewed", async () => {
    await renderPanel([]);
    expect(
      container.querySelector(".collector-scope-note")?.textContent,
    ).toContain("尚未选择范围");
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
    expect(
      container.querySelector(".collector-scope-note")?.textContent,
    ).toContain("0 个股票池、1 个手工标的，去重后 1 个标的");
  });

  it("combines multiple industry pools with explicit companies", async () => {
    const pool = (
      id: string,
      name: string,
      instruments: string[],
    ): ComponentProps<typeof CollectorPanel>["snapshots"][number] => ({
      snapshot_id: id,
      universe_id: `industry-${id}`,
      version: 1,
      name,
      observed_at: "2026-01-02T00:00:00Z",
      market_scope: ["CN"],
      content_sha256: "a".repeat(64),
      reference_issues: [],
      members: instruments.map((instrument_id) => ({
        instrument: { instrument_id, name: instrument_id, exchange: "SH" },
        affiliations: [],
      })),
    });
    await renderPanel(
      [],
      [
        pool("pool-a", "银行", ["601288.SH", "600036.SH"]),
        pool("pool-b", "保险", ["600036.SH", "601318.SH"]),
      ],
    );
    const group = container.querySelector('[aria-label="独立采集股票池"]')!;
    const boxes = group.querySelectorAll<HTMLInputElement>(
      'input[type="checkbox"]',
    );
    await act(async () => boxes.forEach((box) => box.click()));
    const instruments = container.querySelector<HTMLInputElement>(
      'input[aria-label="独立采集标的"]',
    )!;
    const setValue = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!;
    await act(async () => {
      setValue.call(instruments, "000001.SZ");
      instruments.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const note = container.querySelector(".collector-scope-note");
    expect(note?.textContent).toContain("2 个股票池、1 个手工标的");
    expect(note?.textContent).toContain("去重后 4 个标的");
  });

  it("does not let a slower previous selection replace the latest selection", async () => {
    const first = deferred<{ job: CollectorJob }>();
    const second = deferred<{ job: CollectorJob }>();
    actions.readCollectorJob
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    await renderPanel([makeJob(firstId), makeJob(secondId)]);

    await selectJob(firstId);
    await selectJob(secondId);
    await act(async () =>
      second.resolve({ job: makeJob(secondId, "running", "第二任务") }),
    );
    expect(
      container
        .querySelector(".collector-run")
        ?.getAttribute("data-collector-job"),
    ).toBe(secondId);

    await act(async () =>
      first.resolve({ job: makeJob(firstId, "completed", "第一任务") }),
    );
    expect(
      container
        .querySelector(".collector-run")
        ?.getAttribute("data-collector-job"),
    ).toBe(secondId);
    expect(container.querySelector(".collector-run")?.textContent).toContain(
      "第二任务",
    );
    expect(
      container.querySelector<HTMLSelectElement>(
        'select[aria-label="查看已保存采集任务"]',
      )?.value,
    ).toBe(secondId);
  });
});
