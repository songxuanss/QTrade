import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { WorkspaceRuntime } from "../../src/features/auth/server";
import { TradingConsole } from "../../src/features/trading/console";

vi.mock("../../src/app/workspace/runtime-actions", () => ({
  controlRuntime: vi.fn(),
}));

const runtime: WorkspaceRuntime = {
  runtime_id: "11111111-1111-4111-8111-111111111111",
  workspace_id: "22222222-2222-4222-8222-222222222222",
  account_id: "33333333-3333-4333-8333-333333333333",
  strategy_version_id: "44444444-4444-4444-8444-444444444444",
  mode: "paper",
  state: "running",
  revision: 3,
  updated_at: "2026-10-05T00:00:00Z",
};

describe("TradingConsole", () => {
  it("requires explicit typed confirmation for bulk or terminal actions", () => {
    const html = renderToStaticMarkup(
      createElement(TradingConsole, {
        runtimes: [runtime],
        error: false,
        canControl: true,
        canClose: true,
      }),
    );

    expect(html).toContain('name="confirmation"');
    expect(html).toContain('pattern="CONFIRM STOP"');
    expect(html).toContain('pattern="CONFIRM CANCEL_ORDERS"');
    expect(html).toContain('pattern="CONFIRM CLOSE_POSITIONS"');
    expect(html).toContain("实盘启动需审批");
    expect(html).toContain("预交易风控");
  });

  it("does not render destructive controls without control permissions", () => {
    const html = renderToStaticMarkup(
      createElement(TradingConsole, {
        runtimes: [runtime],
        error: false,
        canControl: false,
        canClose: false,
      }),
    );

    expect(html).not.toContain('name="confirmation"');
    expect(html).not.toContain(">停止运行</button>");
    expect(html).toContain("运行中");
  });
});
