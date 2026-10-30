import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import HomePage from "../../src/app/page";

vi.mock("../../src/features/auth/server", () => ({
  currentSession: async () => false,
}));

describe("public entry safety", () => {
  it("shows the platform state without offering unauthenticated trading controls", async () => {
    const html = renderToStaticMarkup(await HomePage());
    expect(html).toContain("等待身份连接");
    expect(html).not.toMatch(/<(form|input)\b/);
    expect(html).toContain("disabled");
    expect(html).not.toMatch(/(access_token|refresh_token|client_secret)/);
  });
});
