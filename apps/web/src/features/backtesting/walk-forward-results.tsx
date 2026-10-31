"use client";

import Link from "next/link";
import type { BacktestWalkForwardRunInfo } from "../auth/server";

const windowStatusLabels: Record<
  BacktestWalkForwardRunInfo["windows"][number]["status"],
  string
> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  cancelled: "已取消",
  failed: "失败",
};

const batchStatusLabels: Record<BacktestWalkForwardRunInfo["status"], string> =
  {
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    partial: "部分完成",
    failed: "失败",
  };

function percent(value: string | null | undefined) {
  if (value == null) return "—";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

export function WalkForwardBatchResults({
  batch,
}: {
  batch: BacktestWalkForwardRunInfo;
}) {
  return (
    <div className="walk-forward-batch">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">WALK-FORWARD RESULT</p>
          <h3>
            {batch.instrument_id} · {batch.windows.length} 个验证窗口
          </h3>
        </div>
        <span
          className={`backtest-status status-${batch.status}`}
          role="status"
        >
          {batchStatusLabels[batch.status]}
        </span>
      </div>
      {batch.aggregate && (
        <dl className="walk-forward-summary">
          <div>
            <dt>完成窗口</dt>
            <dd>{batch.aggregate.completed_windows}</dd>
          </div>
          <div>
            <dt>失败窗口</dt>
            <dd>{batch.aggregate.failed_windows}</dd>
          </div>
          <div>
            <dt>验证均值收益</dt>
            <dd>{percent(batch.aggregate.mean_validation_return)}</dd>
          </div>
          <div>
            <dt>验证中位收益</dt>
            <dd>{percent(batch.aggregate.median_validation_return)}</dd>
          </div>
          <div>
            <dt>窗口复利收益</dt>
            <dd>{percent(batch.aggregate.compounded_validation_return)}</dd>
          </div>
          <div>
            <dt>正收益窗口占比</dt>
            <dd>{percent(batch.aggregate.positive_window_ratio)}</dd>
          </div>
          <div>
            <dt>平均 / 最差回撤</dt>
            <dd>
              {percent(batch.aggregate.mean_max_drawdown)} /{" "}
              {percent(batch.aggregate.worst_max_drawdown)}
            </dd>
          </div>
          <div>
            <dt>验证窗口成交数</dt>
            <dd>{batch.aggregate.total_trade_count}</dd>
          </div>
        </dl>
      )}
      <div className="backtest-table-scroll walk-forward-results">
        <table className="backtest-comparison-table">
          <thead>
            <tr>
              <th scope="col">窗口</th>
              <th scope="col">验证区间</th>
              <th scope="col">验证样本</th>
              <th scope="col">状态</th>
              <th scope="col">收益</th>
              <th scope="col">最大回撤</th>
              <th scope="col">成交</th>
              <th scope="col">运行</th>
            </tr>
          </thead>
          <tbody>
            {batch.windows.map((window) => (
              <tr key={window.run_id}>
                <th scope="row">{window.index}</th>
                <td>
                  {new Date(window.validation_start).toLocaleString("zh-CN")} –{" "}
                  {new Date(window.validation_end).toLocaleString("zh-CN")}
                </td>
                <td>{window.validation_samples.toLocaleString("zh-CN")}</td>
                <td>
                  <span className={`backtest-status status-${window.status}`}>
                    {windowStatusLabels[window.status]}
                  </span>
                  {window.error_code && (
                    <span className="walk-forward-error">
                      {" "}
                      {window.error_code}
                    </span>
                  )}
                </td>
                <td>{percent(window.total_return)}</td>
                <td>{percent(window.max_drawdown)}</td>
                <td>{window.trade_count ?? "—"}</td>
                <td>
                  <Link
                    className="walk-forward-run-link"
                    href={`/workspace/backtests/${window.run_id}`}
                  >
                    报告
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="backtest-model-note" role="note">
        汇总只统计已完成窗口；失败/取消窗口单独计数，不进入均值。
        每次读取都会重新校验各窗口的不可变结果和 manifest。
      </p>
    </div>
  );
}
