"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { FormEvent, useMemo, useState } from "react";
import type {
  BacktestDatasetInfo,
  BacktestRunInfo,
  WorkspaceStrategy,
} from "../auth/server";
import {
  cancelBacktest,
  submitBacktest,
} from "../../app/workspace/backtest-actions";
import { WalkForwardPlanner } from "./walk-forward";

const statusLabels: Record<BacktestRunInfo["status"], string> = {
  queued: "排队中",
  running: "运行中",
  cancelled: "已取消",
  failed: "失败",
  completed: "已完成",
};

function localDateTime(value: string) {
  const date = new Date(value);
  const pad = (part: number) => String(part).padStart(2, "0");
  const milliseconds = String(date.getMilliseconds()).padStart(3, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${milliseconds}`;
}

function localRangeStart(value: string) {
  const subMillisecond = value.match(/\.(\d+)/)?.[1]?.slice(3) ?? "";
  const needsRoundUp = /[1-9]/.test(subMillisecond);
  const milliseconds = Date.parse(value) + (needsRoundUp ? 1 : 0);
  return localDateTime(new Date(milliseconds).toISOString());
}

export function BacktestWorkbench({
  runs,
  datasets,
  strategies,
  canRun,
  error,
}: {
  runs: BacktestRunInfo[];
  datasets: BacktestDatasetInfo[];
  strategies: WorkspaceStrategy[];
  canRun: boolean;
  error: boolean;
}) {
  const router = useRouter();
  const [strategyId, setStrategyId] = useState(
    strategies[0]?.strategy_id ?? "",
  );
  const [datasetId, setDatasetId] = useState(datasets[0]?.dataset_id ?? "");
  const selectedDataset = useMemo(
    () => datasets.find((item) => item.dataset_id === datasetId),
    [datasets, datasetId],
  );
  const [start, setStart] = useState(
    selectedDataset ? localRangeStart(selectedDataset.start) : "",
  );
  const [end, setEnd] = useState(
    selectedDataset ? localDateTime(selectedDataset.end) : "",
  );
  const [initialCash, setInitialCash] = useState("100000");
  const [currency, setCurrency] = useState("CNY");
  const [parametersText, setParametersText] = useState("{}");
  const [benchmarkId, setBenchmarkId] = useState("");
  const [lotSize, setLotSize] = useState("100");
  const [feeBps, setFeeBps] = useState("3");
  const [slippageBps, setSlippageBps] = useState("5");
  const [participation, setParticipation] = useState("0.1");
  const [dailyLimitPercent, setDailyLimitPercent] = useState("10");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const comparableRuns = runs
    .filter((run) => run.status === "completed")
    .slice(0, 2);

  async function createRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrorMessage("");
    setMessage("");
    if (!strategyId || !datasetId || !start || !end) {
      setErrorMessage("请选择策略和数据集，并填写回测时间范围。");
      return;
    }
    if (new Date(start).getTime() >= new Date(end).getTime()) {
      setErrorMessage("开始时间必须早于结束时间。");
      return;
    }
    let parameters: Record<string, unknown>;
    try {
      const parsed = JSON.parse(parametersText) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        throw Error("parameters must be an object");
      parameters = parsed as Record<string, unknown>;
    } catch {
      setErrorMessage("参数必须是有效的 JSON 对象。");
      return;
    }
    setBusy(true);
    const strategy = strategies.find((item) => item.strategy_id === strategyId);
    const result = await submitBacktest({
      strategyId,
      version: strategy?.current_version ?? 1,
      datasetId,
      start,
      end,
      initialCash,
      currency,
      costModelVersion: "cost.v1",
      parameters,
      executionModel: {
        lot_size: Number(lotSize),
        fee_bps: feeBps,
        slippage_bps: slippageBps,
        max_volume_fraction: participation,
        latency_bars: 1,
        session_open: "09:30:00",
        session_close: "15:00:00",
        timezone_name: "Asia/Shanghai",
        daily_price_limit_fraction: dailyLimitPercent
          ? (Number(dailyLimitPercent) / 100).toString()
          : null,
        session_breaks: [["11:30:00", "13:00:00"]],
        ...(selectedDataset?.granularity === "1d"
          ? { periods_per_year: 252 }
          : selectedDataset?.granularity === "1m"
            ? { periods_per_year: 252 * 240 }
            : selectedDataset?.granularity === "5m"
              ? { periods_per_year: 252 * 48 }
              : selectedDataset?.granularity === "15m"
                ? { periods_per_year: 252 * 16 }
                : selectedDataset?.granularity === "1h"
                  ? { periods_per_year: 252 * 4 }
                  : {}),
      },
      ...(benchmarkId.trim() ? { benchmarkId: benchmarkId.trim() } : {}),
    });
    setBusy(false);
    if (!result.ok) {
      setErrorMessage(result.error ?? "回测创建失败。");
      return;
    }
    setMessage("回测已排队，输入快照已冻结。");
    router.refresh();
  }

  async function cancel(runId: string) {
    setErrorMessage("");
    const result = await cancelBacktest(runId);
    if (!result.ok) {
      setErrorMessage(result.error ?? "取消任务失败。");
      return;
    }
    setMessage("已提交取消，状态已更新。");
    router.refresh();
  }

  return (
    <div className="workspace-page backtest-workbench-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">RESEARCH / BACKTEST</p>
          <h1>回测实验</h1>
          <p className="page-description">
            固定策略版本、数据集、参数与运行环境，再排队执行历史模拟。
          </p>
        </div>
      </div>
      {error && (
        <p className="strategy-message error" role="alert">
          回测服务或历史记录暂不可用。
        </p>
      )}
      {errorMessage && (
        <p className="strategy-message error" role="alert">
          {errorMessage}
        </p>
      )}
      {message && (
        <p className="strategy-message success" role="status">
          {message}
        </p>
      )}
      <div className="backtest-layout">
        <section
          className="panel backtest-create-panel"
          aria-labelledby="backtest-create-title"
        >
          <div className="panel-heading">
            <div>
              <p className="eyebrow">NEW RUN</p>
              <h2 id="backtest-create-title">配置回测</h2>
            </div>
            <span className="backtest-lock-mark" aria-hidden="true">
              ◆
            </span>
          </div>
          {!canRun ? (
            <p className="backtest-empty">当前角色没有创建回测任务的权限。</p>
          ) : strategies.length === 0 ? (
            <p className="backtest-empty">
              请先在策略研究中创建并校验策略版本。
            </p>
          ) : datasets.length === 0 ? (
            <p className="backtest-empty">
              暂无已发布的数据集，请先完成行情采集和质量发布。
            </p>
          ) : (
            <form className="backtest-form" onSubmit={createRun}>
              <label>
                <span>策略版本</span>
                <select
                  value={strategyId}
                  onChange={(event) => setStrategyId(event.target.value)}
                >
                  {strategies
                    .filter((item) => !item.archived)
                    .map((item) => (
                      <option key={item.strategy_id} value={item.strategy_id}>
                        {item.name} · v{item.current_version}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                <span>Market Dataset</span>
                <select
                  value={datasetId}
                  onChange={(event) => {
                    const nextId = event.target.value;
                    const item = datasets.find(
                      (candidate) => candidate.dataset_id === nextId,
                    );
                    setDatasetId(nextId);
                    if (item) {
                      setStart(localRangeStart(item.start));
                      setEnd(localDateTime(item.end));
                    }
                  }}
                >
                  {datasets.map((item) => (
                    <option key={item.dataset_id} value={item.dataset_id}>
                      v{item.version} ·{" "}
                      {item.instrument_ids.slice(0, 3).join(", ")} ·{" "}
                      {item.granularity}
                    </option>
                  ))}
                </select>
                <small>
                  {selectedDataset?.content_sha256.slice(0, 16)} ·
                  数据集内容摘要
                </small>
              </label>
              <div className="backtest-date-grid">
                <label>
                  <span>开始时间</span>
                  <input
                    type="datetime-local"
                    step="0.001"
                    value={start}
                    onChange={(event) => setStart(event.target.value)}
                    required
                  />
                </label>
                <label>
                  <span>结束时间</span>
                  <input
                    type="datetime-local"
                    step="0.001"
                    value={end}
                    onChange={(event) => setEnd(event.target.value)}
                    required
                  />
                </label>
              </div>
              <label>
                <span>日内价格限制（%），留空表示不限制</span>
                <input
                  type="number"
                  min="0"
                  max="99"
                  step="0.1"
                  value={dailyLimitPercent}
                  onChange={(event) => setDailyLimitPercent(event.target.value)}
                />
              </label>
              <label>
                <span>Strategy Version 参数</span>
                <textarea
                  value={parametersText}
                  onChange={(event) => setParametersText(event.target.value)}
                  rows={4}
                  spellCheck={false}
                  aria-label="回测参数 JSON"
                />
                <small>
                  参数会按当前不可变版本的 schema 校验，默认值会写入 manifest。
                </small>
              </label>
              <label>
                <span>基准标的（可选）</span>
                <input
                  value={benchmarkId}
                  onChange={(event) => setBenchmarkId(event.target.value)}
                  placeholder="例如 000300.SH"
                />
              </label>
              <div className="backtest-date-grid">
                <label>
                  <span>最小交易单位（股）</span>
                  <input
                    type="number"
                    min="1"
                    step="1"
                    value={lotSize}
                    onChange={(event) => setLotSize(event.target.value)}
                    required
                  />
                </label>
                <label>
                  <span>手续费（基点）</span>
                  <input
                    type="number"
                    min="0"
                    step="0.1"
                    value={feeBps}
                    onChange={(event) => setFeeBps(event.target.value)}
                    required
                  />
                </label>
              </div>
              <div className="backtest-date-grid">
                <label>
                  <span>滑点（基点）</span>
                  <input
                    type="number"
                    min="0"
                    step="0.1"
                    value={slippageBps}
                    onChange={(event) => setSlippageBps(event.target.value)}
                    required
                  />
                </label>
                <label>
                  <span>最大成交量占比</span>
                  <input
                    type="number"
                    min="0.001"
                    max="1"
                    step="any"
                    value={participation}
                    onChange={(event) => setParticipation(event.target.value)}
                    required
                  />
                </label>
              </div>
              <div className="backtest-date-grid">
                <label>
                  <span>初始资金</span>
                  <input
                    type="number"
                    min="0.01"
                    step="0.01"
                    value={initialCash}
                    onChange={(event) => setInitialCash(event.target.value)}
                    required
                  />
                </label>
                <label>
                  <span>计价币种</span>
                  <select
                    value={currency}
                    onChange={(event) => setCurrency(event.target.value)}
                  >
                    <option value="CNY">CNY · 人民币</option>
                    <option value="USD">USD · 美元</option>
                  </select>
                </label>
              </div>
              <div className="backtest-frozen-note">
                <strong>不可变运行输入</strong>
                <span>
                  A 股时段 Asia/Shanghai · 午休 11:30–13:00 · 信号延迟 1 根 K 线
                </span>
              </div>
              <button
                className="button button-primary"
                type="submit"
                disabled={busy}
              >
                {busy ? "正在创建…" : "创建 Backtest Run"}
              </button>
            </form>
          )}
        </section>
        <section
          className="panel backtest-runs-panel"
          aria-labelledby="backtest-runs-title"
        >
          <div className="panel-heading">
            <div>
              <p className="eyebrow">RUN HISTORY</p>
              <h2 id="backtest-runs-title">最近任务</h2>
            </div>
            <span className="backtest-count">{runs.length}</span>
          </div>
          {comparableRuns.length === 2 && (
            <Link
              className="button button-secondary backtest-compare-link"
              href={`/workspace/backtests/compare?run_id=${comparableRuns[0].run_id}&run_id=${comparableRuns[1].run_id}`}
            >
              对比最近两次完成回测
            </Link>
          )}
          {runs.length === 0 ? (
            <p className="backtest-empty">提交后的回测任务会显示在这里。</p>
          ) : (
            <ol className="backtest-run-list">
              {runs.map((run) => (
                <li key={run.run_id}>
                  <div className="backtest-run-topline">
                    <div>
                      {run.status === "completed" || run.status === "failed" ? (
                        <Link
                          className="backtest-run-report-link"
                          href={`/workspace/backtests/${run.run_id}`}
                        >
                          Run {run.run_id.slice(0, 8)} · 查看报告
                        </Link>
                      ) : (
                        <strong>Run {run.run_id.slice(0, 8)}</strong>
                      )}
                      <span className={`backtest-status status-${run.status}`}>
                        {statusLabels[run.status]}
                      </span>
                    </div>
                    <time dateTime={run.created_at}>
                      {new Date(run.created_at).toLocaleString("zh-CN")}
                    </time>
                  </div>
                  <p>
                    Version {run.strategy_version_id.slice(0, 8)} · Dataset{" "}
                    {run.dataset_id.slice(0, 8)}
                  </p>
                  <div
                    className="backtest-progress-track"
                    role="progressbar"
                    aria-label={`回测进度 ${run.progress}%`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={run.progress}
                  >
                    <span style={{ width: `${run.progress}%` }} />
                  </div>
                  <div className="backtest-run-bottomline">
                    <span>
                      {run.progress}% · {run.initial_cash} {run.currency}
                    </span>
                    {canRun &&
                      (run.status === "queued" || run.status === "running") && (
                        <button
                          type="button"
                          className="button button-quiet"
                          onClick={() => void cancel(run.run_id)}
                        >
                          取消任务
                        </button>
                      )}
                    {run.error_code && (
                      <span className="backtest-error-code">
                        {run.error_code}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
      <WalkForwardPlanner datasets={datasets} />
    </div>
  );
}
