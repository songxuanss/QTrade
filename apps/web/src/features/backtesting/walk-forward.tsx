"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { WalkForwardBatchResults } from "./walk-forward-results";
import type {
  BacktestDatasetInfo,
  BacktestWalkForwardPlanInfo,
  BacktestWalkForwardRunInfo,
  WorkspaceStrategy,
} from "../auth/server";
import {
  refreshWalkForward,
  startWalkForward,
  submitWalkForwardPlan,
} from "../../app/workspace/backtest-actions";

type ParameterRule = {
  type?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  title?: string;
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

function toLocalInput(value: string, roundUp = false) {
  const subMillisecond = value.match(/\.(\d+)/)?.[1]?.slice(3) ?? "";
  const date = new Date(
    new Date(value).getTime() +
      (roundUp && /[1-9]/.test(subMillisecond) ? 1 : 0),
  );
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 23);
}

export function WalkForwardPlanner({
  datasets,
  strategies,
  canRun,
}: {
  datasets: BacktestDatasetInfo[];
  strategies: WorkspaceStrategy[];
  canRun: boolean;
}) {
  const supported = datasets.filter(
    (dataset) => dataset.granularity !== "tick",
  );
  const runnableStrategies = useMemo(
    () => strategies.filter((item) => !item.archived),
    [strategies],
  );
  const [datasetId, setDatasetId] = useState(supported[0]?.dataset_id ?? "");
  const selected = supported.find(
    (dataset) => dataset.dataset_id === datasetId,
  );
  const [instrumentId, setInstrumentId] = useState(
    selected?.instrument_ids[0] ?? "",
  );
  const [start, setStart] = useState(
    selected ? toLocalInput(selected.start, true) : "",
  );
  const [end, setEnd] = useState(selected ? toLocalInput(selected.end) : "");
  const [trainDays, setTrainDays] = useState("20");
  const [validationDays, setValidationDays] = useState("5");
  const [stepDays, setStepDays] = useState("5");
  const [strategyId, setStrategyId] = useState(
    runnableStrategies[0]?.strategy_id ?? "",
  );
  const [version, setVersion] = useState(
    runnableStrategies[0]?.current_version ?? 1,
  );
  const [initialCash, setInitialCash] = useState("100000");
  const [currency, setCurrency] = useState("CNY");
  const [gridText, setGridText] = useState("");
  const [parameters, setParameters] = useState<Record<string, unknown>>(() =>
    parameterDefaults(
      runnableStrategies[0]?.strategy_versions?.find(
        (item) => item.version === runnableStrategies[0]?.current_version,
      )?.parameter_schema,
    ),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [plan, setPlan] = useState<BacktestWalkForwardPlanInfo | null>(null);
  const [batch, setBatch] = useState<BacktestWalkForwardRunInfo | null>(null);

  const selectedStrategy = strategies.find(
    (item) => item.strategy_id === strategyId,
  );
  const versions = selectedStrategy?.strategy_versions ?? [];
  const selectedVersion = versions.find((item) => item.version === version);
  const parameterRules = Object.entries(
    (selectedVersion?.parameter_schema?.properties ?? {}) as Record<
      string,
      ParameterRule
    >,
  );

  const fields = useMemo(
    () =>
      [
        ["训练窗口（天）", trainDays, setTrainDays],
        ["验证窗口（天）", validationDays, setValidationDays],
        ["滚动步长（天）", stepDays, setStepDays],
      ] as const,
    [trainDays, validationDays, stepDays],
  );

  const pending = batch?.status === "queued" || batch?.status === "running";

  useEffect(() => {
    if (!batch || !pending) return;
    const timer = window.setInterval(async () => {
      const result = await refreshWalkForward(batch.walk_forward_id);
      if (result.run) setBatch(result.run);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [batch, pending]);

  function chooseVersion(nextStrategyId: string, nextVersion: number) {
    setStrategyId(nextStrategyId);
    setVersion(nextVersion);
    const next = strategies.find((item) => item.strategy_id === nextStrategyId);
    setParameters(
      parameterDefaults(
        next?.strategy_versions?.find((item) => item.version === nextVersion)
          ?.parameter_schema,
      ),
    );
  }

  function executionModel() {
    const intraday = selected?.granularity !== "1d";
    return {
      lot_size: 100,
      fee_bps: "3",
      minimum_fee: "5",
      sell_tax_bps: "5",
      slippage_bps: "5",
      tick_size: "0.01",
      settlement_days: 1,
      max_volume_fraction: "0.1",
      latency_bars: 1,
      ...(intraday
        ? {
            session_open: "09:30:00",
            session_close: "15:00:00",
            timezone_name: "Asia/Shanghai",
            session_breaks: [["11:30:00", "13:00:00"]],
          }
        : {}),
      daily_price_limit_fraction: "0.1",
      periods_per_year: selected?.granularity === "1d" ? 252 : 240,
    };
  }

  function validSelection() {
    if (!selected || !instrumentId || new Date(start) >= new Date(end)) {
      setError("请选择数据集与标的，并确认时间范围有效。");
      return false;
    }
    return true;
  }

  async function preview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    setPlan(null);
    if (!validSelection()) return;
    setBusy(true);
    const seconds = (value: string) => Math.round(Number(value) * 86_400);
    const result = await submitWalkForwardPlan({
      datasetId,
      instrumentId,
      start,
      end,
      trainWindowSeconds: seconds(trainDays),
      validationWindowSeconds: seconds(validationDays),
      stepSeconds: seconds(stepDays),
    });
    setBusy(false);
    if (!result.plan) {
      setError(result.error ?? "Walk-forward 计划创建失败。");
      return;
    }
    setPlan(result.plan);
  }

  async function run() {
    setError("");
    setMessage("");
    if (!validSelection()) return;
    if (!strategyId || !initialCash) {
      setError("请选择策略版本并填写初始资金。");
      return;
    }
    let parameterGrid: Record<string, unknown[]> | null = null;
    if (gridText.trim()) {
      try {
        const parsed = JSON.parse(gridText) as unknown;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("grid");
        }
        parameterGrid = parsed as Record<string, unknown[]>;
        if (
          Object.values(parameterGrid).some(
            (values) => !Array.isArray(values) || values.length === 0,
          )
        ) {
          throw new Error("grid");
        }
      } catch {
        setError('参数网格必须是形如 {"fast":[5,10],"slow":[20,30]} 的 JSON。');
        return;
      }
    }
    setBusy(true);
    const seconds = (value: string) => Math.round(Number(value) * 86_400);
    const result = await startWalkForward({
      strategyId,
      version,
      datasetId,
      instrumentId,
      start,
      end,
      trainWindowSeconds: seconds(trainDays),
      validationWindowSeconds: seconds(validationDays),
      stepSeconds: seconds(stepDays),
      initialCash,
      currency,
      costModelVersion: "cost.v1",
      parameters,
      executionModel: executionModel(),
      parameterGrid,
    });
    setBusy(false);
    if (!result.run) {
      setError(result.error ?? "Walk-forward 运行创建失败。");
      return;
    }
    setPlan(null);
    setBatch(result.run);
    setMessage(
      `已排队 ${result.run.windows.length} 个验证窗口，每个窗口都是一个独立回测。`,
    );
  }

  return (
    <section
      className="panel walk-forward-panel"
      aria-labelledby="walk-forward-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">OUT-OF-SAMPLE REVIEW</p>
          <h2 id="walk-forward-title">Walk-forward 样本外运行</h2>
        </div>
      </div>
      <p className="page-description">
        用数据集内的真实 bar
        时间戳切分滚动训练/验证区间，并为每个验证窗口排队真实回测；
        训练区间仅用于切分核对，当前不做参数寻优，验证结果是对冻结参数的样本外评估。
      </p>
      {supported.length === 0 ? (
        <p className="backtest-empty">
          当前没有可用于切分的已发布 bar 数据集。
        </p>
      ) : (
        <form className="backtest-form walk-forward-form" onSubmit={preview}>
          <label>
            <span>Market Dataset</span>
            <select
              value={datasetId}
              onChange={(event) => {
                const next = supported.find(
                  (dataset) => dataset.dataset_id === event.target.value,
                );
                setDatasetId(event.target.value);
                setInstrumentId(next?.instrument_ids[0] ?? "");
                setStart(next ? toLocalInput(next.start, true) : "");
                setEnd(next ? toLocalInput(next.end) : "");
              }}
            >
              {supported.map((dataset) => (
                <option key={dataset.dataset_id} value={dataset.dataset_id}>
                  {dataset.instrument_ids.length > 1
                    ? `${dataset.instrument_ids.length} 标的（${dataset.instrument_ids.slice(0, 2).join(", ")}${dataset.instrument_ids.length > 2 ? "…" : ""}）`
                    : dataset.instrument_ids.slice(0, 2).join(", ")}{" "}
                  · {dataset.granularity} · v{dataset.version} ·{" "}
                  {dataset.start.slice(0, 10)} → {dataset.end.slice(0, 10)} ·{" "}
                  {dataset.dataset_id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Instrument</span>
            <select
              value={instrumentId}
              onChange={(event) => setInstrumentId(event.target.value)}
            >
              {selected?.instrument_ids.map((instrument) => (
                <option key={instrument} value={instrument}>
                  {instrument}
                </option>
              ))}
            </select>
          </label>
          <div className="backtest-date-grid">
            <label>
              <span>切分起点</span>
              <input
                type="datetime-local"
                step="0.001"
                value={start}
                onChange={(event) => setStart(event.target.value)}
                required
              />
            </label>
            <label>
              <span>切分终点</span>
              <input
                type="datetime-local"
                step="0.001"
                value={end}
                onChange={(event) => setEnd(event.target.value)}
                required
              />
            </label>
          </div>
          <div className="backtest-date-grid">
            {fields.map(([label, value, onChange]) => (
              <label key={label}>
                <span>{label}</span>
                <input
                  type="number"
                  min="0.001"
                  max="3650"
                  step="any"
                  value={value}
                  onChange={(event) => onChange(event.target.value)}
                  required
                />
              </label>
            ))}
          </div>
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
              {runnableStrategies.flatMap((strategy) =>
                (strategy.strategy_versions ?? []).map((item) => (
                  <option
                    key={`${strategy.strategy_id}:${item.version}`}
                    value={`${strategy.strategy_id}:${item.version}`}
                  >
                    {strategy.name} · v{item.version}
                  </option>
                )),
              )}
            </select>
          </label>
          {parameterRules.length > 0 && (
            <div className="backtest-date-grid">
              {parameterRules.map(([name, rule]) => (
                <label key={name}>
                  <span>{rule.title ?? name}</span>
                  <input
                    type={
                      rule.type === "integer" || rule.type === "number"
                        ? "number"
                        : "text"
                    }
                    min={rule.minimum}
                    max={rule.maximum}
                    step={rule.type === "integer" ? 1 : "any"}
                    value={String(parameters[name] ?? "")}
                    onChange={(event) =>
                      setParameters((current) => ({
                        ...current,
                        [name]:
                          rule.type === "integer" || rule.type === "number"
                            ? event.target.value === ""
                              ? ""
                              : Number(event.target.value)
                            : event.target.value,
                      }))
                    }
                  />
                </label>
              ))}
            </div>
          )}
          <label>
            <span>参数网格（可选 JSON）</span>
            <textarea
              rows={2}
              value={gridText}
              onChange={(event) => setGridText(event.target.value)}
              placeholder={'{"fast":[5,10],"slow":[20,30]}'}
            />
          </label>
          <div className="backtest-date-grid">
            <label>
              <span>初始资金</span>
              <input
                type="number"
                min="1"
                step="1000"
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
          <div className="walk-forward-actions">
            <button className="button button-secondary" disabled={busy}>
              {busy ? "正在处理…" : "预览切分"}
            </button>
            <button
              type="button"
              className="button button-primary"
              onClick={run}
              disabled={busy || !canRun || runnableStrategies.length === 0}
            >
              运行 Walk-forward
            </button>
          </div>
          <p className="backtest-model-note" role="note">
            执行假设与回测配置默认一致：整手 100、手续费 3bps、最低佣金 5
            元、卖出印花税 5bps、0.01
            最小价位、T+1。填写参数网格时，每个窗口先在训练区间
            评估全部候选并选择样本内收益最高者（同收益取回撤更小），再在验证区间执行选中参数；
            每个窗口独立使用初始资金，不做跨窗口资金复利，窗口失败会在汇总中显式计数。
          </p>
        </form>
      )}
      {!canRun && (
        <p className="backtest-empty">
          当前角色可以查看切分，但没有创建 Walk-forward 运行的权限。
        </p>
      )}
      {error && (
        <p className="strategy-message error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="strategy-message success" role="status">
          {message}
        </p>
      )}
      {plan && (
        <div className="backtest-table-scroll walk-forward-results">
          <table className="backtest-comparison-table">
            <thead>
              <tr>
                <th scope="col">窗口</th>
                <th scope="col">训练区间</th>
                <th scope="col">训练样本</th>
                <th scope="col">验证区间</th>
                <th scope="col">验证样本</th>
              </tr>
            </thead>
            <tbody>
              {plan.windows.map((window, index) => (
                <tr key={`${window.validation_start}-${index}`}>
                  <th scope="row">{index + 1}</th>
                  <td>
                    {new Date(window.train_start).toLocaleString("zh-CN")} –{" "}
                    {new Date(window.train_end).toLocaleString("zh-CN")}
                  </td>
                  <td>{window.train_samples.toLocaleString("zh-CN")}</td>
                  <td>
                    {new Date(window.validation_start).toLocaleString("zh-CN")}{" "}
                    – {new Date(window.validation_end).toLocaleString("zh-CN")}
                  </td>
                  <td>{window.validation_samples.toLocaleString("zh-CN")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {batch && <WalkForwardBatchResults batch={batch} />}
    </section>
  );
}
