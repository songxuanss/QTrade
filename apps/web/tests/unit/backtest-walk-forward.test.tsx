import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { BacktestWalkForwardRunInfo } from "../../src/features/auth/server";
import { WalkForwardBatchResults } from "../../src/features/backtesting/walk-forward-results";

const batch: BacktestWalkForwardRunInfo = {
  walk_forward_id: "11111111-1111-4111-8111-111111111111",
  created_at: "2026-01-06T00:00:00Z",
  strategy_id: "22222222-2222-4222-8222-222222222222",
  strategy_version: 1,
  dataset_id: "33333333-3333-4333-8333-333333333333",
  instrument_id: "SSE:600000",
  start: "2026-01-01T00:00:00Z",
  end: "2026-01-09T00:00:00Z",
  initial_cash: "100000",
  currency: "CNY",
  benchmark_id: null,
  cost_model_version: "cost.v1",
  parameters_json: "{}",
  execution_config_json: "{}",
  random_seed: 0,
  train_window_seconds: 86400,
  validation_window_seconds: 86400,
  step_seconds: 86400,
  parameter_grid_json: '{"fast":[5,10]}',
  status: "partial",
  windows: [
    {
      index: 1,
      train_start: "2026-01-01T00:00:00Z",
      train_end: "2026-01-02T00:00:00Z",
      validation_start: "2026-01-02T00:00:00Z",
      validation_end: "2026-01-03T00:00:00Z",
      train_samples: 1,
      validation_samples: 1,
      run_id: "44444444-4444-4444-8444-444444444444",
      status: "completed",
      total_return: "0.02",
      max_drawdown: "0.01",
      sharpe_ratio: "1.5",
      trade_count: 2,
      error_code: null,
      selected_parameters_json: '{"slow":20,"fast":5}',
      in_sample_return: "0.01",
      candidate_count: 2,
    },
    {
      index: 2,
      train_start: "2026-01-02T00:00:00Z",
      train_end: "2026-01-03T00:00:00Z",
      validation_start: "2026-01-03T00:00:00Z",
      validation_end: "2026-01-04T00:00:00Z",
      train_samples: 1,
      validation_samples: 1,
      run_id: "55555555-5555-4555-8555-555555555555",
      status: "failed",
      total_return: null,
      max_drawdown: null,
      sharpe_ratio: null,
      trade_count: null,
      error_code: "engine_error",
      selected_parameters_json: null,
      in_sample_return: null,
      candidate_count: 2,
    },
  ],
  aggregate: {
    completed_windows: 1,
    failed_windows: 1,
    mean_validation_return: "0.02",
    median_validation_return: "0.02",
    mean_max_drawdown: "0.01",
    worst_max_drawdown: "0.01",
    positive_window_ratio: "1",
    compounded_validation_return: "0.02",
    total_trade_count: 2,
  },
};

describe("walk-forward result view", () => {
  it("shows per-window outcomes, failure counts and the frozen-behaviour note", () => {
    const markup = renderToStaticMarkup(
      createElement(WalkForwardBatchResults, { batch }),
    );

    expect(markup).toContain("SSE:600000 · 2 个验证窗口");
    expect(markup).toContain("部分完成");
    expect(markup).toContain("2.00%");
    expect(markup).toContain("engine_error");
    expect(markup).toContain("已完成");
    expect(markup).toContain("失败");
    expect(markup).toContain("失败/取消窗口单独计数");
    expect(markup).toContain("参数 {&quot;slow&quot;:20,&quot;fast&quot;:5}");
    expect(markup).toContain("样本内");
    expect(markup).toContain("候选");
    expect(markup).toContain(
      "/workspace/backtests/44444444-4444-4444-8444-444444444444",
    );
  });

  it("hides the aggregate card when no window has completed", () => {
    const markup = renderToStaticMarkup(
      createElement(WalkForwardBatchResults, {
        batch: {
          ...batch,
          status: "failed",
          aggregate: null,
          windows: batch.windows.map((window) => ({
            ...window,
            status: "failed" as const,
            total_return: null,
            max_drawdown: null,
            sharpe_ratio: null,
            trade_count: null,
            error_code: "engine_error",
          })),
        },
      }),
    );

    expect(markup).not.toContain("walk-forward-summary");
    expect(markup).not.toContain("验证中位收益");
    expect(markup).toContain("失败");
  });
});
