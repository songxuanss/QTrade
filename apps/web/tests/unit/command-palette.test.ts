import { describe, expect, it } from "vitest";
import {
  permittedPages,
  searchablePages,
} from "../../src/features/workbench/command-permissions";

const hrefs = (role: Parameters<typeof permittedPages>[0]) =>
  permittedPages(role).map((page) => page.href);

describe("command palette workspace permissions", () => {
  it("keeps viewers on readable market and data pages", () => {
    expect(hrefs("viewer")).toEqual(["/workspace/market", "/workspace/data"]);
  });

  it("matches resource entry points only when the role can open them", () => {
    expect(searchablePages("viewer", "Order")).toEqual([]);
    expect(searchablePages("viewer", "Strategy")).toEqual([]);
    expect(
      searchablePages("analyst", "Strategy").map((page) => page.href),
    ).toEqual(["/workspace/strategies"]);
    expect(searchablePages("trader", "Order").map((page) => page.href)).toEqual(
      ["/workspace/trading"],
    );
    expect(
      searchablePages("risk_manager", "Alert").map((page) => page.href),
    ).toEqual(["/workspace/alerts"]);
  });

  it("exposes only the role's research, trading, and governance pages", () => {
    expect(hrefs("analyst")).toEqual([
      "/workspace/market",
      "/workspace/strategies",
      "/workspace/backtests",
      "/workspace/alerts",
      "/workspace/data",
    ]);
    expect(hrefs("trader")).toEqual([
      "/workspace/market",
      "/workspace/trading",
      "/workspace/alerts",
      "/workspace/data",
    ]);
    expect(hrefs("risk_manager")).toEqual([
      "/workspace/market",
      "/workspace/risk",
      "/workspace/alerts",
      "/workspace/data",
    ]);
    expect(hrefs("admin")).toHaveLength(8);
  });
});
