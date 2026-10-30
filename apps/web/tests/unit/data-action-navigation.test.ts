import { beforeEach, describe, expect, it, vi } from "vitest";

const ports = vi.hoisted(() => ({
  create: vi.fn(async () => "success"),
  rerun: vi.fn(async () => "success"),
  claim: vi.fn(async () => "success"),
  close: vi.fn(async () => "success"),
  publish: vi.fn(async () => "research_saved"),
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
import {
  claimIssueAction,
  closeIssueAction,
  createTickTaskAction,
  publishResearchAction,
  rerunTaskAction,
} from "../../src/app/workspace/data-actions";

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
    expect(query.get("data_view")).toBe("assets");
    expect(query.get("asset_view")).toBe(asset);
  });
  it("shows newly accepted compatibility downloads in the task list", async () => {
    ports.create.mockResolvedValueOnce("success");
    const query = await destination(createTickTaskAction);
    expect(query.get("data_view")).toBe("assets");
    expect(query.get("asset_view")).toBe("tasks");
  });
  it("keeps rejected download input corrections in the download area", async () => {
    ports.create.mockResolvedValueOnce("invalid");
    const query = await destination(createTickTaskAction);
    expect(query.get("data_view")).toBe("ticks");
    expect(query.get("result")).toBe("invalid");
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
});
