import { afterEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("../auth/workspace-request", () => ({ workspaceRequest: vi.fn() }));
import { workspaceRequest } from "../auth/workspace-request";
import { workspaceCollector } from "./collector-server";
afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
});
it("consumes fast response bodies before a slow failed metadata request reaches their deadline", async () => {
  vi.useFakeTimers();
  const catalog = Response.json([{ source: "fixture" }]);
  setTimeout(() => {
    void catalog.body?.cancel().catch(() => {});
  }, 5);
  vi.mocked(workspaceRequest).mockImplementation(async (path) => {
    if (path === "data/collector/catalog") return catalog;
    if (path === "data/collector/factor-catalog") {
      await new Promise((resolve) => setTimeout(resolve, 20));
      return Response.json({ detail: { code: "timeout" } }, { status: 503 });
    }
    return Response.json(path.endsWith("preferences") ? null : []);
  });
  const result = workspaceCollector();
  await vi.advanceTimersByTimeAsync(20);
  const snapshot = await result;
  expect(snapshot.available).toBe(true);
  expect(snapshot.catalog).toEqual([{ source: "fixture" }]);
  expect(snapshot.factorsAvailable).toBe(false);
  expect(snapshot.jobs).toEqual([]);
});

it("preserves stale definitions and refresh failure evidence from the independent catalog", async () => {
  const catalog = {
    schema_version: 1,
    source: "mairui.factor.history",
    status: "stale",
    definitions: [{ factor_id: "pe_ttm", name: "PE" }],
    fetched_at: null,
    error_code: "timeout",
  };
  vi.mocked(workspaceRequest).mockImplementation(async (path) =>
    Response.json(
      path.endsWith("factor-catalog")
        ? catalog
        : path.endsWith("preferences")
          ? null
          : [],
    ),
  );
  const snapshot = await workspaceCollector();
  expect(snapshot.factorsAvailable).toBe(true);
  expect(snapshot.factorCatalog?.status).toBe("stale");
  expect(snapshot.factorCatalog?.error_code).toBe("timeout");
  expect(snapshot.factors[0].factor_id).toBe("pe_ttm");
});
