import { afterEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("./session-access", () => ({
  readSession: async () => ({
    config: { apiOrigin: "http://fixture.test" },
    name: "fixture-session",
    cookie: "fixture",
    workspaceId: "scope",
  }),
}));
import { workspaceRequest } from "./workspace-request";
afterEach(() => vi.unstubAllGlobals());
it("isolates body consumption when server rendering reuses a fetch response", async () => {
  const shared = Response.json([{ source: "fixture" }]);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => shared),
  );
  const first = await workspaceRequest("data/collector/catalog");
  expect(await first?.json()).toEqual([{ source: "fixture" }]);
  const second = await workspaceRequest("data/collector/catalog");
  expect(await second?.json()).toEqual([{ source: "fixture" }]);
});
