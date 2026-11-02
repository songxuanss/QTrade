"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import type {
  BacktestDatasetInfo,
  BacktestRunInfo,
  MembershipInfo,
  WorkspaceStrategy,
} from "../auth/server";
import {
  cancelBacktest,
  submitBacktest,
} from "../../app/workspace/backtest-actions";
import { WalkForwardPlanner } from "./walk-forward";
import { MembershipPanel } from "./membership-panel";

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

type ParameterRule = {
  type?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  enum?: unknown[];
  title?: string;
  description?: string;
};

function parameterDefaults(schema: Record<string, unknown> | undefined) {
  const properties = schema?.properties;
  if (
    !properties ||
    typeof properties !== "object" ||
    Array.isArray(properties)
  )
    return {};
  const result: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(properties)) {
    const rule = value as ParameterRule;
    if (rule && typeof rule === "object" && "default" in rule)
      result[name] = rule.default;
  }
  return result;
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
  memberships,
  canRun,
  error,
}: {
  runs: BacktestRunInfo[];
  datasets: BacktestDatasetInfo[];
  strategies: WorkspaceStrategy[];
  memberships: MembershipInfo[];
  canRun: boolean;
  error: boolean;
}) {
  const router = useRouter();
  const runnableStrategies = useMemo(
    () => strategies.filter((item) => !item.archived),
    [strategies],
  );
  const [strategyId, setStrategyId] = useState(
    runnableStrategies[0]?.strategy_id ?? "",
  );
  const [version, setVersion] = useState(
    runnableStrategies[0]?.current_version ?? 1,
  );
  const selectedStrategy = strategies.find(
    (item) => item.strategy_id === strategyId,
  );
  const availableVersions = selectedStrategy?.strategy_versions ?? [];
  const selectedVersion = availableVersions.find(
    (item) => item.version === version,
  );
  const parameterRules = Object.entries(
    (selectedVersion?.parameter_schema?.properties ?? {}) as Record<
      string,
      ParameterRule
    >,
  );
  const runnableDatasets = useMemo(() => datasets, [datasets]);
  const [datasetId, setDatasetId] = useState(
    runnableDatasets[0]?.dataset_id ?? "",
  );
  const selectedDataset = useMemo(
    () => runnableDatasets.find((item) => item.dataset_id === datasetId),
    [runnableDatasets, datasetId],
  );
  const [start, setStart] = useState(
    selectedDataset ? localRangeStart(selectedDataset.start) : "",
  );
  const [end, setEnd] = useState(
    selectedDataset ? localDateTime(selectedDataset.end) : "",
  );
  const [initialCash, setInitialCash] = useState("100000");
  const [currency, setCurrency] = useState("CNY");
  const [parameters, setParameters] = useState<Record<string, unknown>>(() =>
    parameterDefaults(selectedVersion?.parameter_schema),
  );
  const [benchmarkId, setBenchmarkId] = useState("");
  const [lotSize, setLotSize] = useState("100");
  const [feeBps, setFeeBps] = useState("3");
  const [slippageBps, setSlippageBps] = useState("5");
  const [tickAggregation, setTickAggregation] = useState("60");
  const [adjustmentFactorMode, setAdjustmentFactorMode] = useState<
    "freeze" | "vendor_adjusted"
  >("freeze");
  const [impactBps, setImpactBps] = useState("0");
  const [limitQueueFraction, setLimitQueueFraction] = useState("1");
  const [tickSize, setTickSize] = useState("0.01");
  const [settlementDays, setSettlementDays] = useState("1");
  const [minimumFee, setMinimumFee] = useState("5");
  const [sellTaxBps, setSellTaxBps] = useState("5");
  const [participation, setParticipation] = useState("0.1");
  const [dailyLimitPercent, setDailyLimitPercent] = useState("10");
  const [universeMode, setUniverseMode] = useState<"dataset" | "membership">(
    "dataset",
  );
  const [rightsPolicy, setRightsPolicy] = useState<"subscribe" | "skip">(
    "subscribe",
  );
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const comparableRuns = runs
    .filter((run) => run.status === "completed")
    .slice(0, 2);
  const strategyLabel = (run: BacktestRunInfo) => {
    for (const strategy of strategies) {
      const match = strategy.strategy_versions?.find(
        (candidate) =>
          candidate.strategy_version_id === run.strategy_version_id,
      );
      if (match) return `${strategy.name} · v${match.version}`;
    }
    return `版本 ${run.strategy_version_id.slice(0, 8)}`;
  };
  const datasetLabel = (run: BacktestRunInfo) => {
    const dataset = datasets.find((item) => item.dataset_id === run.dataset_id);
    return dataset
      ? `${dataset.instrument_ids.slice(0, 2).join(", ")} · ${dataset.granularity} · v${dataset.version}`
      : `数据集 ${run.dataset_id.slice(0, 8)}`;
  };
  const pendingRuns = runs.some(
    (run) => run.status === "queued" || run.status === "running",
  );

  useEffect(() => {
    if (!pendingRuns) return;
    const timer = window.setInterval(() => router.refresh(), 4000);
    return () => window.clearInterval(timer);
  }, [pendingRuns, router]);

  function chooseVersion(nextStrategyId: string, nextVersion: number) {
    setStrategyId(nextStrategyId);
    setVersion(nextVersion);
    const next = strategies.find((item) => item.strategy_id === nextStrategyId);
    const info = next?.strategy_versions?.find(
      (candidate) => candidate.version === nextVersion,
    );
    setParameters(parameterDefaults(info?.parameter_schema));
  }

  function toggleCompare(runId: string) {
    setCompareIds((current) =>
      current.includes(runId)
        ? current.filter((id) => id !== runId)
        : current.length >= 20
          ? current
          : [...current, runId],
    );
  }

  function updateParameter(name: string, rule: ParameterRule, raw: string) {
    setParameters((current) => ({
      ...current,
      [name]:
        rule.type === "integer" || rule.type === "number"
          ? raw === ""
            ? ""
            : Number(raw)
          : raw,
    }));
  }

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
    setBusy(true);
    const intraday = selectedDataset?.granularity !== "1d";
    const result = await submitBacktest({
      strategyId,
      version,
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
        minimum_fee: minimumFee,
        sell_tax_bps: sellTaxBps,
        slippage_bps: slippageBps,
        tick_aggregation_seconds: Number(tickAggregation),
        impact_bps: impactBps,
        limit_queue_fraction: limitQueueFraction,
        tick_size: tickSize,
        settlement_days: Number(settlementDays),
        max_volume_fraction: participation,
        latency_bars: 1,
        // Daily bars are stamped at exchange midnight Asia/Shanghai; the
        // trading date (and any ex-date corporate action) must use that zone.
        timezone_name: "Asia/Shanghai",
        ...(intraday
          ? {
              session_open: "09:30:00",
              session_close: "15:00:00",
              session_breaks: [["11:30:00", "13:00:00"]],
            }
          : {}),
        daily_price_limit_fraction: dailyLimitPercent
          ? (Number(dailyLimitPercent) / 100).toString()
          : null,
        ...(selectedDataset?.granularity === "1d"
          ? { periods_per_year: 252 }
          : selectedDataset?.granularity === "1m"
            ? { periods_per_year: 252 * 240 }
            : selectedDataset?.granularity === "5m"
              ? { periods_per_year: 252 * 48 }
              : selectedDataset?.granularity === "15m"
                ? { periods_per_year: 252 * 16 }
                : selectedDataset?.granularity === "30m"
                  ? { periods_per_year: 252 * 8 }
                  : selectedDataset?.granularity === "1h"
                    ? { periods_per_year: 252 * 4 }
                    : {}),
      },
      ...(benchmarkId.trim() ? { benchmarkId: benchmarkId.trim() } : {}),
      universeMode,
      rightsPolicy,
      adjustmentFactorMode,
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
          ) : runnableDatasets.length === 0 ? (
            <p className="backtest-empty">
              暂无已发布的数据集，请先完成行情采集和质量发布。
            </p>
          ) : (
            <form className="backtest-form" onSubmit={createRun}>
              <label>
                <span>策略版本</span>
                <select
                  value={`${strategyId}:${version}`}
                  onChange={(event) => {
                    const [nextStrategy, nextVersion] =
                      event.target.value.split(":");
                    chooseVersion(nextStrategy, Number(nextVersion));
                  }}
                >
                  {runnableStrategies.map((item) =>
                    item.strategy_versions?.length ? (
                      <optgroup key={item.strategy_id} label={item.name}>
                        {item.strategy_versions.map((candidate) => (
                          <option
                            key={candidate.strategy_version_id}
                            value={`${item.strategy_id}:${candidate.version}`}
                          >
                            {item.name} · v{candidate.version}
                          </option>
                        ))}
                      </optgroup>
                    ) : (
                      <option
                        key={item.strategy_id}
                        value={`${item.strategy_id}:${item.current_version}`}
                      >
                        {item.name} · v{item.current_version}
                      </option>
                    ),
                  )}
                </select>
                <small>
                  {selectedVersion
                    ? `代码摘要 ${selectedVersion.code_sha256.slice(0, 12)}`
                    : "参数与源码在提交时冻结到 manifest"}
                </small>
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
                  {runnableDatasets.map((item) => (
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
              <div className="backtest-date-grid">
                <label>
                  <span>最小价格变动（元）</span>
                  <input
                    type="number"
                    min="0"
                    step="0.001"
                    value={tickSize}
                    onChange={(event) => setTickSize(event.target.value)}
                  />
                </label>
                <label>
                  <span>交收规则</span>
                  <select
                    value={settlementDays}
                    onChange={(event) => setSettlementDays(event.target.value)}
                  >
                    <option value="0">T+0（当日可卖）</option>
                    <option value="1">T+1（A 股）</option>
                  </select>
                </label>
              </div>
              <div className="backtest-parameters">
                <span>策略参数</span>
                {parameterRules.length ? (
                  <div className="backtest-parameter-grid">
                    {parameterRules.map(([name, rule]) => {
                      const value = parameters[name] ?? rule.default ?? "";
                      const label = rule.title ?? name;
                      if (rule.type === "boolean")
                        return (
                          <label
                            className="backtest-parameter-check"
                            key={name}
                          >
                            <input
                              type="checkbox"
                              checked={Boolean(value)}
                              onChange={(event) =>
                                setParameters((current) => ({
                                  ...current,
                                  [name]: event.target.checked,
                                }))
                              }
                            />
                            <span>{label}</span>
                          </label>
                        );
                      if (rule.enum)
                        return (
                          <label key={name}>
                            <span>{label}</span>
                            <select
                              value={String(value)}
                              onChange={(event) =>
                                setParameters((current) => ({
                                  ...current,
                                  [name]: event.target.value,
                                }))
                              }
                            >
                              {rule.enum.map((option) => (
                                <option
                                  key={String(option)}
                                  value={String(option)}
                                >
                                  {String(option)}
                                </option>
                              ))}
                            </select>
                            {rule.description && (
                              <small>{rule.description}</small>
                            )}
                          </label>
                        );
                      return (
                        <label key={name}>
                          <span>{label}</span>
                          <input
                            type={
                              rule.type === "integer" || rule.type === "number"
                                ? "number"
                                : "text"
                            }
                            step={rule.type === "integer" ? "1" : "any"}
                            min={rule.minimum}
                            max={rule.maximum}
                            value={String(value)}
                            onChange={(event) =>
                              updateParameter(name, rule, event.target.value)
                            }
                          />
                          {rule.description && (
                            <small>{rule.description}</small>
                          )}
                        </label>
                      );
                    })}
                  </div>
                ) : (
                  <p className="backtest-empty">该版本没有可调参数。</p>
                )}
                <small>
                  参数会按当前不可变版本的 schema 校验，默认值会写入 manifest。
                </small>
              </div>
              <label>
                <span>基准标的（可选）</span>
                <select
                  value={benchmarkId}
                  onChange={(event) => setBenchmarkId(event.target.value)}
                >
                  <option value="">不设置基准</option>
                  {selectedDataset?.instrument_ids.map((instrument) => (
                    <option key={instrument} value={instrument}>
                      {instrument}
                    </option>
                  ))}
                </select>
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
              {selectedDataset?.granularity === "tick" && (
                <label>
                  <span>tick 聚合窗口（秒）</span>
                  <input
                    type="number"
                    min="1"
                    max="3600"
                    step="1"
                    value={tickAggregation}
                    onChange={(event) => setTickAggregation(event.target.value)}
                  />
                </label>
              )}
              <div className="backtest-date-grid">
                <label>
                  <span>市场冲击系数（基点）</span>
                  <input
                    type="number"
                    min="0"
                    step="0.1"
                    value={impactBps}
                    onChange={(event) => setImpactBps(event.target.value)}
                  />
                </label>
                <label>
                  <span>涨跌停队列比例</span>
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.05"
                    value={limitQueueFraction}
                    onChange={(event) =>
                      setLimitQueueFraction(event.target.value)
                    }
                  />
                </label>
              </div>
              <div className="backtest-date-grid">
                <label>
                  <span>最低佣金（元）</span>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={minimumFee}
                    onChange={(event) => setMinimumFee(event.target.value)}
                  />
                </label>
                <label>
                  <span>卖出印花税（基点）</span>
                  <input
                    type="number"
                    min="0"
                    step="0.1"
                    value={sellTaxBps}
                    onChange={(event) => setSellTaxBps(event.target.value)}
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
              <label>
                <span>成员区间校验</span>
                <select
                  value={universeMode}
                  onChange={(event) =>
                    setUniverseMode(
                      event.target.value === "membership"
                        ? "membership"
                        : "dataset",
                    )
                  }
                >
                  <option value="dataset">
                    仅数据集（默认，披露幸存者偏差）
                  </option>
                  <option value="membership">按成员区间交易</option>
                </select>
              </label>
              <label>
                <span>价格口径</span>
                <select
                  value={adjustmentFactorMode}
                  onChange={(event) =>
                    setAdjustmentFactorMode(
                      event.target.value === "vendor_adjusted"
                        ? "vendor_adjusted"
                        : "freeze",
                    )
                  }
                >
                  <option value="freeze">未复权价 + 公司行为账本</option>
                  <option value="vendor_adjusted">
                    供应商复权价（禁用来账本分配）
                  </option>
                </select>
              </label>
              <label>
                <span>配股处理</span>
                <select
                  value={rightsPolicy}
                  onChange={(event) =>
                    setRightsPolicy(
                      event.target.value === "skip" ? "skip" : "subscribe",
                    )
                  }
                >
                  <option value="subscribe">
                    按现金全额认购（不足则部分认购）
                  </option>
                  <option value="skip">不认购（保留现金）</option>
                </select>
              </label>
              <div className="backtest-frozen-note">
                <strong>不可变运行输入</strong>
                <span>
                  {selectedDataset?.granularity === "1d"
                    ? "日线按交易日收盘后可用 · 信号延迟 1 根 K 线 · 无盘中时段过滤"
                    : `${selectedDataset?.granularity ?? "当前周期"} · A 股时段 Asia/Shanghai · 午休 11:30–13:00 · 信号延迟 1 根 K 线`}
                </span>
              </div>
              <p className="backtest-model-note" role="note">
                模型边界：现金分红、送转/拆并已按除权日入账，T+1、整手和最小价位已建模；
                成员校验可启用工作区/供应方区间并在区间结束强制平仓，但区间若非官方历史仍可能有幸存者偏差；
                配股按所选策略认购或跳过；价格口径可选「未复权价 +
                公司行为账本」或「供应商复权价（禁用来账本分配）」； 无 L2
                盘口，结果用于研究比较， 不构成实盘可用性证明。
              </p>
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
            <div className="backtest-run-actions">
              {pendingRuns && <span className="backtest-live-dot" />}
              <button
                type="button"
                className="button button-quiet"
                onClick={() => router.refresh()}
              >
                刷新状态
              </button>
              <span className="backtest-count">{runs.length}</span>
            </div>
          </div>
          <div className="backtest-compare-actions">
            {comparableRuns.length >= 2 && (
              <Link
                className="button button-secondary backtest-compare-link"
                href={`/workspace/backtests/compare?run_id=${comparableRuns[0].run_id}&run_id=${comparableRuns[1].run_id}`}
              >
                对比最近两次完成回测
              </Link>
            )}
            {compareIds.length >= 2 && (
              <Link
                className="button button-primary"
                href={`/workspace/backtests/compare?${compareIds
                  .map((id) => `run_id=${id}`)
                  .join("&")}`}
              >
                对比已选 {compareIds.length} 次
              </Link>
            )}
          </div>
          {runs.length === 0 ? (
            <p className="backtest-empty">提交后的回测任务会显示在这里。</p>
          ) : (
            <ol className="backtest-run-list">
              {runs.map((run) => (
                <li key={run.run_id}>
                  <div className="backtest-run-topline">
                    <div>
                      {run.status === "completed" && (
                        <input
                          className="backtest-run-check"
                          type="checkbox"
                          aria-label={`选择 ${run.run_id.slice(0, 8)} 进行对比`}
                          checked={compareIds.includes(run.run_id)}
                          onChange={() => toggleCompare(run.run_id)}
                        />
                      )}
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
                    {strategyLabel(run)} · {datasetLabel(run)}
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
      <WalkForwardPlanner
        datasets={datasets}
        strategies={strategies}
        canRun={canRun}
      />
      <MembershipPanel memberships={memberships} canManage={canRun} />
    </div>
  );
}
