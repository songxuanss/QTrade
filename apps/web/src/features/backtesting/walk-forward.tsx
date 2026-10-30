"use client";

import { FormEvent, useMemo, useState } from "react";
import type { BacktestDatasetInfo } from "../auth/server";
import { submitWalkForwardPlan } from "../../app/workspace/backtest-actions";
import type { BacktestWalkForwardPlanInfo } from "../auth/server";

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
}: {
  datasets: BacktestDatasetInfo[];
}) {
  const supported = datasets.filter(
    (dataset) => dataset.granularity !== "tick",
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [plan, setPlan] = useState<BacktestWalkForwardPlanInfo | null>(null);

  const fields = useMemo(
    () =>
      [
        ["训练窗口（天）", trainDays, setTrainDays],
        ["验证窗口（天）", validationDays, setValidationDays],
        ["滚动步长（天）", stepDays, setStepDays],
      ] as const,
    [trainDays, validationDays, stepDays],
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setPlan(null);
    if (!selected || !instrumentId || new Date(start) >= new Date(end)) {
      setError("请选择数据集与标的，并确认时间范围有效。");
      return;
    }
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

  return (
    <section
      className="panel walk-forward-panel"
      aria-labelledby="walk-forward-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">OUT-OF-SAMPLE REVIEW</p>
          <h2 id="walk-forward-title">Walk-forward 时间切分</h2>
        </div>
      </div>
      <p className="page-description">
        使用数据集内的实际 bar
        时间戳规划滚动训练与验证区间；验证窗口与训练窗口分离，样本不足时拒绝生成。
      </p>
      {supported.length === 0 ? (
        <p className="backtest-empty">
          当前没有可用于切分的已发布 bar 数据集。
        </p>
      ) : (
        <form className="backtest-form walk-forward-form" onSubmit={submit}>
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
                  {dataset.dataset_id.slice(0, 8)} · {dataset.granularity}
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
          <button className="button button-secondary" disabled={busy}>
            {busy ? "正在计算样本…" : "生成 Walk-forward 切分"}
          </button>
        </form>
      )}
      {error && (
        <p className="strategy-message error" role="alert">
          {error}
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
    </section>
  );
}
