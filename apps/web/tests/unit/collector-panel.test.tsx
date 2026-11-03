// @vitest-environment happy-dom

import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CollectorPanel } from "../../src/features/data/collector-panel";
import { previewCollector } from "../../src/app/workspace/collector-actions";
import type {
  CollectorJob,
  CollectorPreferences,
  CollectorSource,
} from "../../src/features/data/collector-types";

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
    expect(group.textContent).toContain("已选 2");
    expect(group.querySelectorAll("label.is-selected")).toHaveLength(2);
    expect(group.textContent).toContain("v1 · 2 个成员");
  });

  it("estimates 7-day segments and offers the yearly Pro source", async () => {
    const source = (id: string, name: string): CollectorSource => ({
      source: id,
      name,
      provider: "mairui",
      dataset_kind: "bars",
      periods: ["1d", "5m"],
      instrument_kinds: ["stock"],
      markets: ["CN"],
      timezone: "Asia/Shanghai",
      configured: true,
      evidence: "configured",
      limitations: [],
    });
    await act(async () => {
      root.render(
        <CollectorPanel
          catalog={[
            source("mairui.history.bars", "MaiRui 日线/分钟历史"),
            source("mairui.pro.bars", "MaiRui Pro 日线/分钟历史"),
          ]}
          factors={[]}
          factorsAvailable={false}
          initialJobs={[]}
          available
          canManage
          snapshots={[]}
          initialPreferences={{
            schema_version: 1,
            dataset_kind: "bars",
            source: "mairui.history.bars",
            granularity: "5m",
            first: "2024-01-01",
            last: "2024-12-31",
            instruments: "601288.SH, 600036.SH, 600519.SH",
            snapshot_id: null,
            snapshot_ids: [],
            fields: [],
            mode: "missing",
          }}
        />,
      );
    });
    const estimate = container.querySelector(".collector-segment-estimate");
    expect(estimate?.textContent).toContain("159 个采集分段");
    expect(estimate?.textContent).toContain("普通历史分钟接口按最多 7 天拆分");
    const button = estimate?.querySelector<HTMLButtonElement>("button");
    expect(button?.textContent).toContain("改用 Pro 分钟历史");
    await act(async () => button?.click());
    expect(
      container.querySelector<HTMLSelectElement>(
        "select[aria-label='采集数据来源']",
      )?.value,
    ).toBe("mairui.pro.bars");
    expect(estimate?.textContent).toContain("3 个采集分段");
    expect(estimate?.textContent).toContain("当前来源按日历年组织分段");
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

describe("CollectorPanel source defaults and inline feedback", () => {
  let container: HTMLDivElement;
  let root: Root;

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

  const freeDaily = makeSource({
    source: "mairui.history.bars",
    name: "MaiRui 日线/分钟历史",
    periods: ["5m", "15m", "30m", "1h", "1d"],
    tier: "free",
  });
  const paidMinute = makeSource({
    source: "mairui.pro.bars",
    name: "MaiRui Pro 分钟历史",
    periods: ["1m", "5m", "15m", "30m", "1h"],
    tier: "paid",
  });

  function prefs(
    overrides: Partial<CollectorPreferences> = {},
  ): CollectorPreferences {
    return {
      schema_version: 1,
      dataset_kind: "bars",
      source: "",
      granularity: "1d",
      first: null,
      last: null,
      instruments: "",
      snapshot_id: null,
      snapshot_ids: [],
      fields: [],
      mode: "missing",
      ...overrides,
    };
  }

  const setSelectValue = Object.getOwnPropertyDescriptor(
    HTMLSelectElement.prototype,
    "value",
  )!.set!;

  function select(label: string): HTMLSelectElement {
    return container.querySelector<HTMLSelectElement>(
      `select[aria-label="${label}"]`,
    )!;
  }

  async function choose(label: string, value: string) {
    const element = select(label);
    await act(async () => {
      setSelectValue.call(element, value);
      element.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  async function renderCatalog(
    catalog: CollectorSource[],
    initialPreferences: CollectorPreferences | null = null,
  ) {
    await act(async () => {
      root.render(
        <CollectorPanel
          catalog={catalog}
          factors={[]}
          factorsAvailable={false}
          initialJobs={[]}
          available
          canManage
          snapshots={[]}
          initialPreferences={initialPreferences}
        />,
      );
    });
  }

  beforeEach(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    window.history.replaceState(null, "", "/workspace/data");
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

  it("falls back to the free source for daily bars and upgrades to the paid source for a minute period", async () => {
    await renderCatalog([freeDaily, paidMinute]);
    expect(select("采集数据来源").value).toBe("mairui.history.bars");
    const optionTexts = [...select("采集数据来源").options].map(
      (option) => option.textContent,
    );
    expect(optionTexts).toContain("MaiRui 日线/分钟历史 · 免费");
    expect(optionTexts).toContain("MaiRui Pro 分钟历史 · 付费");
    await choose("独立采集周期", "5m");
    expect(select("采集数据来源").value).toBe("mairui.pro.bars");
  });

  it("prefers the configured paid source when it supports the selected period", async () => {
    await renderCatalog([freeDaily, paidMinute], prefs({ granularity: "5m" }));
    expect(select("采集数据来源").value).toBe("mairui.pro.bars");
  });

  it("keeps an explicitly saved source over the paid default", async () => {
    await renderCatalog(
      [freeDaily, paidMinute],
      prefs({ source: "mairui.history.bars", granularity: "5m" }),
    );
    expect(select("采集数据来源").value).toBe("mairui.history.bars");
    await choose("独立采集周期", "15m");
    expect(select("采集数据来源").value).toBe("mairui.history.bars");
  });

  it("shows preview failure inline beside the action buttons in the create view", async () => {
    vi.mocked(previewCollector).mockResolvedValue({ error: "timeout" });
    await renderCatalog(
      [freeDaily, paidMinute],
      prefs({
        source: "mairui.history.bars",
        first: "2024-01-01",
        last: "2024-01-05",
        instruments: "000001.SZ",
      }),
    );
    const form = container.querySelector<HTMLFormElement>(
      "form.collector-form",
    )!;
    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
    });
    await act(async () => {});
    const footerAlert = container.querySelector(
      '.collector-footer [role="alert"]',
    );
    expect(footerAlert?.textContent).toContain("来源请求超时");
    expect(container.querySelectorAll('[role="alert"]')).toHaveLength(1);
  });

  it("explains why the default-range button is disabled when dates are filled", async () => {
    await renderCatalog(
      [freeDaily, paidMinute],
      prefs({ first: "2024-01-01", last: "2024-01-05" }),
    );
    const yearButton = [
      ...container.querySelectorAll<HTMLButtonElement>("button"),
    ].find((button) => button.textContent?.includes("使用最近自然年日期"))!;
    expect(yearButton.disabled).toBe(true);
    expect(container.querySelector(".collector-form")?.textContent).toContain(
      "已填写日期；清空开始与结束日期后可一键填充最近自然年。",
    );
  });

  it("explains why preview is disabled when the only source is unconfigured", async () => {
    await renderCatalog([{ ...freeDaily, configured: false }]);
    expect(select("采集数据来源").value).toBe("mairui.history.bars");
    const previewButton = [
      ...container.querySelectorAll<HTMLButtonElement>("button"),
    ].find((button) => button.textContent?.includes("预览独立采集计划"))!;
    expect(previewButton.disabled).toBe(true);
    expect(container.querySelector(".collector-footer")?.textContent).toContain(
      "当前来源未配置，无法预览采集计划。",
    );
    expect(container.querySelector(".collector-form")?.textContent).toContain(
      "当前来源未配置，无法计算默认区间。",
    );
  });
});
