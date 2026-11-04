import { beforeEach, describe, expect, it, vi } from "vitest";

const ports = vi.hoisted(() => ({
  create: vi.fn(async () => "success"),
  rerun: vi.fn(async () => "success"),
  claim: vi.fn(async () => "success"),
  close: vi.fn(async () => "success"),
  publish: vi.fn(async () => "research_saved"),
  compose: vi.fn(async () => ({
    result: {
      dataset: {
        dataset_id: "4ad3c536-681b-57d3-b2f1-7fc224093106",
        instrument_ids: ["600519.SH", "000001.SZ"],
        start: "2025-10-30T16:00:00Z",
        end: "2026-10-30T16:00:00Z",
        granularity: "1h",
      },
      snapshot_id: "11111111-1111-4111-8111-111111111111",
      selected: [],
      excluded: [],
    },
  })),
  createUniverseSnapshot: vi.fn(async () => ({
    snapshot_id: "22222222-2222-4222-8222-222222222222",
  })),
  redirect: vi.fn((path: string) => {
    throw new Error(path);
  }),
}));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect: ports.redirect }));
vi.mock("../../src/features/auth/server", () => ({
  createTickDataTask: ports.create,
  rerunDataTask: ports.rerun,
  claimDataIssue: ports.claim,
  closeDataIssue: ports.close,
}));
vi.mock("../../src/features/data/research-server", () => ({
  publishResearchDataset: ports.publish,
}));
vi.mock("../../src/features/data/composition-server", () => ({
  composePoolDataset: ports.compose,
}));
vi.mock("../../src/features/data/universe-server", () => ({
  createUniverseSnapshot: ports.createUniverseSnapshot,
}));
import {
  claimIssueAction,
  closeIssueAction,
  composePoolDatasetAction,
  createTickTaskAction,
  publishResearchAction,
  rerunTaskAction,
} from "../../src/app/workspace/data-actions";
import { customUniverseAction } from "../../src/app/workspace/universe-actions";

async function destination(
  action: (form: FormData) => Promise<unknown>,
  form = new FormData(),
) {
  await expect(action(form)).rejects.toThrow();
  expect(ports.redirect).toHaveBeenCalledTimes(1);
  return new URL(ports.redirect.mock.calls[0][0], "http://qtrade.test")
    .searchParams;
}

/** Controlled mutation ports prove where a person returns after the operation;
 * they do not stand in for deployed authorization or supplier acceptance. */
describe("data operation navigation", () => {
  beforeEach(() => vi.clearAllMocks());
  it.each([
    ["rerun", rerunTaskAction, "tasks"],
    ["claim", claimIssueAction, "quality"],
    ["close", closeIssueAction, "quality"],
    ["publish", publishResearchAction, "research"],
  ] as const)("returns %s to its own asset area", async (_, action, asset) => {
    const query = await destination(action);
    expect(query.get("data_view")).toBe("jobs");
    expect(query.get("asset_view")).toBe(asset);
  });
  it("shows newly accepted compatibility downloads in the task list", async () => {
    ports.create.mockResolvedValueOnce("success");
    const form = new FormData();
    form.set("source", "mairui.a-share.tick");
    form.set("authorization_id", "mairui-default");
    form.set("instrument_id", "601288.SH");
    form.set("start", "2026-10-15T00:00");
    form.set("end", "2026-10-16T00:00");
    const query = await destination(createTickTaskAction, form);
    expect(query.get("data_view")).toBe("jobs");
    expect(query.get("asset_view")).toBe("tasks");
    expect(query.get("download_instrument")).toBe("601288.SH");
    expect(query.has("authorization_id")).toBe(false);
  });
  it("keeps safe rejected download input without returning credentials", async () => {
    ports.create.mockResolvedValueOnce("invalid");
    const form = new FormData();
    form.set("source", "mairui.a-share.tick");
    form.set("authorization_id", "mairui-default");
    form.set("instrument_id", "601288.SH");
    form.set("start", "2026-10-16T00:00");
    form.set("end", "2026-10-15T00:00");
    const query = await destination(createTickTaskAction, form);
    expect(query.get("data_view")).toBe("download");
    expect(query.get("research_view")).toBe("ticks");
    expect(query.get("result")).toBe("invalid");
    expect(query.get("download_instrument")).toBe("601288.SH");
    expect(query.get("download_start")).toBe("2026-10-16T00:00");
    expect(query.get("download_end")).toBe("2026-10-15T00:00");
    expect(query.has("authorization_id")).toBe(false);
    expect(query.has("download_authorization_id")).toBe(false);
  });
  it.each(["complete", "partial"])(
    "retains the original selection for %s data",
    async (result) => {
      ports.create.mockResolvedValueOnce(result);
      const form = new FormData();
      form.set("source", "mairui.a-share.tick");
      form.set("instrument_id", "601288.SH");
      form.set("start", "2026-10-15T00:00");
      form.set("end", "2026-10-16T00:00");
      const query = await destination(createTickTaskAction, form);
      expect(query.get("download_instrument")).toBe("601288.SH");
      expect(query.get("download_start")).toBe("2026-10-15T00:00");
      expect(query.get("download_end")).toBe("2026-10-16T00:00");
      expect(query.get("result")).toBe(result);
    },
  );
  it("composes multi-granularity pool datasets and returns the composed dataset id", async () => {
    const form = new FormData();
    form.set("snapshot_id", "11111111-1111-4111-8111-111111111111");
    form.set("granularity", "1h");
    form.set("start_date", "2025-11-03");
    form.set("end_date", "2026-10-30");
    form.set("selection_size", "5");
    const query = await destination(composePoolDatasetAction, form);
    expect(ports.compose).toHaveBeenCalledWith(
      expect.objectContaining({
        snapshot_id: "11111111-1111-4111-8111-111111111111",
        granularity: "1h",
        selection_size: 5,
      }),
    );
    expect(query.get("data_view")).toBe("jobs");
    expect(query.get("asset_view")).toBe("research");
    expect(query.get("result")).toBe("composition_saved");
    expect(query.get("composed_dataset")).toBe(
      "4ad3c536-681b-57d3-b2f1-7fc224093106",
    );
  });
  it("imports and normalizes stock codes from an uploaded CSV file when creating a custom universe", async () => {
    const form = new FormData();
    form.set("name", "CSV 导入组合");
    form.set("instrument_ids", "600519.SH");
    form.set(
      "instrument_file",
      new File(
        ["code,name\n000001,平安银行\n601288.SH,农业银行\n"],
        "pool.csv",
        {
          type: "text/csv",
        },
      ),
    );
    const query = await destination(customUniverseAction, form);
    expect(ports.createUniverseSnapshot).toHaveBeenCalledWith({
      name: "CSV 导入组合",
      instrument_ids: ["600519.SH", "000001.SZ", "601288.SH"],
    });
    expect(query.get("universe_snapshot")).toBe(
      "22222222-2222-4222-8222-222222222222",
    );
  });
});
