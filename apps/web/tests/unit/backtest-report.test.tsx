import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type {
  BacktestReportInfo,
  BacktestManifestInfo,
  BacktestRunInfo,
} from "../../src/features/auth/server";
import { BacktestReport } from "../../src/features/backtesting/report";

const run: BacktestRunInfo = {
  run_id: "11111111-1111-4111-8111-111111111111",
  strategy_version_id: "22222222-2222-4222-8222-222222222222",
  dataset_id: "33333333-3333-4333-8333-333333333333",
  manifest_id: "44444444-4444-4444-8444-444444444444",
  created_at: "2026-01-01T00:00:00Z",
  start: "2026-01-01T00:00:00Z",
  end: "2026-02-01T00:00:00Z",
  initial_cash: "100000",
  currency: "CNY",
  benchmark_id: "SSE:000300",
  cost_model_version: "cost.v1",
  status: "completed",
  progress: 100,
  result_object_key: "backtests/result.json",
  error_code: null,
  revision: 3,
};

const report: BacktestReportInfo = {
  schema_version: 1,
  workspace_id: "00000000-0000-4000-8000-000000000001",
  dataset_id: run.dataset_id,
  strategy_version_id: run.strategy_version_id,
  currency: "CNY",
  determinism: { engine: "qtrade.sandbox-event.v1", seed: 0 },
  equity_curve: [
    {
      timestamp: "2026-01-01T00:00:00Z",
      cash: "100000",
      equity: "100000",
      drawdown: "0",
      benchmark_equity: "100000",
      positions: [],
    },
    {
      timestamp: "2026-01-02T00:00:00Z",
      cash: "95000",
      equity: "105000",
      drawdown: "0",
      benchmark_equity: "103000",
      positions: [
        {
          instrument_id: "SSE:600000",
          quantity: "100",
          market_value: "10000",
        },
      ],
    },
  ],
  orders: [
    {
      instrument_id: "SSE:600000",
      side: "buy",
      quantity: "200",
      filled_quantity: "100",
      submitted_at: "2026-01-01T00:00:00Z",
      filled_at: null,
      price: "50",
      status: "partially_filled_end_of_data",
    },
  ],
  fills: [
    {
      instrument_id: "SSE:600000",
      side: "buy",
      filled_quantity: "100",
      filled_at: "2026-01-02T00:00:00Z",
      price: "50",
      fee: "5",
    },
  ],
  summary: {
    initial_cash: "100000",
    final_equity: "105000",
    total_return: "0.05",
    ending_drawdown: "0",
    sample_count: 2,
  },
  metrics: {
    max_drawdown: "0.02",
    annualized_return: "1.2",
    annualized_volatility: "0.4",
    sharpe_ratio: "1.5",
    sortino_ratio: null,
    benchmark_return: "0.03",
    calmar_ratio: "60",
    win_rate: "1",
    profit_factor: null,
    turnover: "0.05",
    average_exposure: "0.2",
    max_exposure: "0.4",
    trade_count: 1,
    winning_trades: 1,
    losing_trades: 0,
  },
};

const manifest: BacktestManifestInfo = {
  manifest_id: run.manifest_id,
  dataset_sha256: "a".repeat(64),
  code_sha256: "b".repeat(64),
  engine_name: "qtrade.sandbox-event.v1",
  engine_version: "1",
  adapter_version: "1",
  environment_image_digest: `sha256:${"c".repeat(64)}`,
  parameters_json: JSON.stringify({ fast: 5, slow: 20 }),
  random_seed: 7,
  execution_config_json: JSON.stringify({
    execution_model: {
      lot_size: 100,
      fee_bps: "3",
      slippage_bps: "5",
      max_volume_fraction: "0.1",
      session_open: "09:30:00",
      session_close: "15:00:00",
      timezone_name: "Asia/Shanghai",
      daily_price_limit_fraction: "0.1",
    },
  }),
  execution_config_sha256: "d".repeat(64),
};

describe("BacktestReport", () => {
  it("renders metrics, an accessible equity chart and execution details", () => {
    const html = renderToStaticMarkup(
      createElement(BacktestReport, { run, report, manifest }),
    );
    expect(html).toContain("回测报告");
    expect(html).toContain("SSE:000300");
    expect(html).toContain("backtest-benchmark-line");
    expect(html).toContain("权益曲线");
    expect(html).toContain("回撤曲线");
    expect(html).toContain('aria-label="组合与基准对比');
    expect(html).toContain("1.5");
    expect(html).toContain("SSE:600000");
    expect(html).toContain("期末持仓");
    expect(html).toContain("部分成交，剩余未成交");
    expect(html).toContain("胜 / 负 / 已平仓");
    expect(html).toContain("cost.v1");
    expect(html).toContain("Asia/Shanghai");
    expect(html).toContain("d".repeat(64));
  });

  it("shows membership coverage, disclosure and forced-exit reasons", () => {
    const membershipManifest: BacktestManifestInfo = {
      ...manifest,
      execution_config_json: JSON.stringify({
        ...(JSON.parse(manifest.execution_config_json) as Record<
          string,
          unknown
        >),
        universe: {
          mode: "membership",
          instruments: 1,
          full_coverage: 0,
          partial_coverage: 1,
          unknown: 0,
          disclosure:
            "已按 1 个标的的成员区间回放：0 个全程覆盖、1 个区间不完整、0 个未知。",
          memberships: [
            {
              instrument_id: "SSE:600000",
              effective_from: "2026-01-01T00:00:00Z",
              effective_to: "2026-01-15T00:00:00Z",
              source: "workspace.definition",
              evidence: "研究口径定义",
              content_version: "e".repeat(64),
            },
          ],
        },
      }),
    };
    const forcedExit = {
      ...report.orders[0],
      side: "sell" as const,
      status: "filled" as const,
      reason: "membership_end" as const,
    };
    const html = renderToStaticMarkup(
      createElement(BacktestReport, {
        run,
        report: { ...report, orders: [forcedExit] },
        manifest: membershipManifest,
      }),
    );

    expect(html).toContain("成员区间与幸存者偏差");
    expect(html).toContain("区间不完整");
    expect(html).toContain("workspace.definition");
    expect(html).toContain("成员区间结束");
    expect(html).toContain("1 个区间不完整");
  });

  it("explains unavailable and failed reports", () => {
    const html = renderToStaticMarkup(
      createElement(BacktestReport, {
        run: { ...run, status: "failed", error_code: "engine_error" },
        report: null,
        manifest: null,
      }),
    );
    expect(html).toContain("回测未完成");
    expect(html).toContain("engine_error");
  });
});
