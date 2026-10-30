import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PortfolioPanel } from "../../src/features/workbench/portfolio-panel";

describe("portfolio panel", () => {
  it("shows an explicit empty state when no account is connected", () => {
    const html = renderToStaticMarkup(
      createElement(PortfolioPanel, { summary: null, error: false }),
    );
    expect(html).toContain("暂无组合数据");
    expect(html).toContain("连接 Paper 账户后");
  });

  it("keeps service errors separate from a genuine empty portfolio", () => {
    const html = renderToStaticMarkup(
      createElement(PortfolioPanel, { summary: null, error: true }),
    );
    expect(html).toContain('role="alert"');
    expect(html).toContain("暂时无法读取组合数据");
    expect(html).not.toContain("暂无组合数据");
  });
});
