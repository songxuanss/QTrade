import { beforeEach, expect, it, vi } from "vitest";
const request = vi.hoisted(() => vi.fn());
vi.mock("../auth/workspace-request", () => ({ workspaceRequest: request }));
import { readSourceProfiles } from "../../app/workspace/source-profile-actions";
beforeEach(() => request.mockReset());
const page = (scope: string, source: string) =>
  Response.json({
    scope_id: scope,
    profiles: [{ source }],
  });
it("merges independently owned discovery groups only within one scope", async () => {
  request
    .mockResolvedValueOnce(page("scope", "prices"))
    .mockResolvedValueOnce(page("scope", "members"));
  const value = await readSourceProfiles();
  expect(value.catalog?.profiles.map((p) => p.source)).toEqual([
    "prices",
    "members",
  ]);
  expect(value.missing).toEqual([]);
});
it("reports the missing group instead of presenting a partial result as complete", async () => {
  request
    .mockResolvedValueOnce(page("scope", "prices"))
    .mockResolvedValueOnce(null);
  const value = await readSourceProfiles();
  expect(value.catalog?.profiles).toHaveLength(1);
  expect(value.missing).toEqual(["当前成员说明"]);
});
it("retains independently available reference notes when collection is offline", async () => {
  request
    .mockRejectedValueOnce(new Error("controlled transport"))
    .mockResolvedValueOnce(page("scope", "members"));
  expect((await readSourceProfiles()).missing).toEqual(["行情与研究采集说明"]);
});
it("rejects cross-scope, duplicate identity and complete service failure", async () => {
  for (const responses of [
    [page("one", "prices"), page("two", "members")],
    [page("scope", "same"), page("scope", "same")],
    [null, null],
  ]) {
    request
      .mockResolvedValueOnce(responses[0])
      .mockResolvedValueOnce(responses[1]);
    expect(await readSourceProfiles()).toEqual({ error: "unavailable" });
  }
});
