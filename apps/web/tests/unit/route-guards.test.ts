import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { proxy } from "../../src/proxy";

afterEach(() => vi.unstubAllEnvs());
describe("route guards before streaming", () => {
  it("blocks development previews in production", () => {
    vi.stubEnv("NODE_ENV", "production");
    const response = proxy(
      new NextRequest("https://qtrade.test/design-system"),
    );
    expect(response.status).toBe(404);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
  it("permits development preview only in development", () => {
    vi.stubEnv("NODE_ENV", "development");
    expect(
      proxy(new NextRequest("http://localhost/design-system")).status,
    ).toBe(200);
  });
  it("rejects unknown workspace sections and permits known ones", () => {
    expect(
      proxy(new NextRequest("https://qtrade.test/workspace/unknown")).status,
    ).toBe(404);
    expect(
      proxy(new NextRequest("https://qtrade.test/workspace/strategies")).status,
    ).toBe(200);
  });
});
