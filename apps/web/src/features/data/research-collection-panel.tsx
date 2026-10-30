"use client";
import { budgetWaitMessage } from "./budget-wait";
import { ResearchPlanControls } from "./research-plan-controls";
import { ResearchJobHistory } from "./research-job-history";
import { ResearchProcessingFailures } from "./research-processing-failures";
import { ResearchResponseReceipts } from "./research-response-receipts";
import { ResearchReplayControl } from "./research-replay";

import { useState } from "react";
import styles from "./research-collection.module.css";
import {
  previewResearch,
  submitResearch,
  listResearchJobs,
  readResearchPublication,
  queryResearch,
  retryResearchJob,
} from "../../app/workspace/research-collection-actions";
import type {
  ResearchCapability,
  ResearchJob,
  ResearchPlan,
} from "./research-collection-types";

type Statement = {
  instrument_id: string;
  statement: string;
  report_period: string;
  disclosure_date: string | null;
  content_version?: string;
  metrics: {
    field_id: string;
    name: string;
    original_value: string | null;
    original_unit: { unit: string | null };
    missing_reason: string | null;
  }[];
};
const states: Record<string, string> = {
  queued: "排队中",
  running: "采集中",
  waiting: "等待预算 / 预留",
  completed: "已发布",
  no_observations: "无观测",
  failed: "失败",
};
const errors: Record<string, string> = {
  forbidden: "当前账号无操作权限，请检查工作区角色。",
  unavailable: "研究采集服务暂不可用，请稍后刷新。",
  unauthorized: "来源授权未通过，请检查服务端证书和权益。",
  invalid_input: "请检查证券代码和报告期范围。",
  unverified: "数据校验未通过，原始证据保留，尚未发布。",
  unsupported: "来源不支持所选类别、市场或查询方式。",
  conflict: "计划或版本发生冲突，请重新预览。",
  timeout: "来源请求超时，请检查任务和来源健康状态。",
};

export function ResearchCollectionPanel({
  catalog,
  initialJobs,
  available,
  canManage,
  instrumentNames = {},
}: {
  catalog: ResearchCapability[];
  initialJobs: ResearchJob[];
  available: boolean;
  canManage: boolean;
  instrumentNames?: Record<string, string>;
}) {
  const [source, setSource] = useState(catalog[0]?.source ?? "");
  const [instrument, setInstrument] = useState("600519.SH");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [plan, setPlan] = useState<ResearchPlan | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [jobs, setJobs] = useState(initialJobs);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [records, setRecords] = useState<Statement[]>([]);
  const [version, setVersion] = useState("");
  const [quality, setQuality] = useState("");
  const selected = catalog.find((c) => c.source === source);
  async function refresh() {
    const result = await listResearchJobs();
    if (result.error) setError(result.error);
    else
      setJobs(
        result.value.filter(
          (job: ResearchJob) => job.request.kind === "financial_statement",
        ),
      );
  }
  async function preview() {
    setBusy(true);
    setError("");
    setPlan(null);
    try {
      const result = await previewResearch({
        source,
        kind: "financial_statement",
        instrument_ids: [instrument.trim().toUpperCase()],
        date_axis: "report_period",
        start,
        end,
        mode: "all",
      });
      if (result.error) setError(result.error);
      else {
        setPlan(result.value ?? null);
        setConfirmed(false);
      }
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    if (!plan) return;
    setBusy(true);
    setError("");
    try {
      const result = await submitResearch(plan, confirmed);
      if (result.error) setError(result.error);
      else {
        setPlan(null);
        await refresh();
      }
    } finally {
      setBusy(false);
    }
  }
  async function retry(job: ResearchJob) {
    setBusy(true);
    setError("");
    try {
      const result = await retryResearchJob(job.request_id);
      if (result.error) setError(result.error);
      else await refresh();
    } catch {
      setError("unavailable");
    } finally {
      setBusy(false);
    }
  }
  async function browse(job: ResearchJob) {
    if (!job.publication_id) return;
    setBusy(true);
    setError("");
    setRecords([]);
    setVersion("");
    try {
      const manifest = await readResearchPublication(job.publication_id);
      if (manifest.error) {
        setError(manifest.error);
        return;
      }
      const result = await queryResearch({
        schema_version: 2,
        publication_id: job.publication_id,
        content_version: manifest.value.content_version,
        kind: job.request.kind,
        limit: 100,
      });
      if (result.error) setError(result.error);
      else {
        setRecords(result.value.records);
        setVersion(manifest.value.content_version);
        setQuality(manifest.value.quality.status);
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className={`panel collector-workbench ${styles.workspace}`}
      id="financial-collection"
      aria-labelledby="financial-title"
    >
      <div className="data-section-heading">
        <div>
          <p className="eyebrow">RESEARCH DATA</p>
          <h2 id="financial-title">财务报表采集</h2>
        </div>
        <button
          className="button button-secondary"
          disabled={busy}
          onClick={refresh}
        >
          刷新财务任务
        </button>
      </div>
      <p className="page-description">
        按报告期采集普通历史三表，保留同报告期的披露版本。披露日期不等于历史可知时间，当前数据不支持
        PIT 查询。
      </p>
      {!available && (
        <p role="status">
          财务采集连接暂不可用，请刷新或检查独立服务。已有任务不会因此被删除。
        </p>
      )}
      {error && (
        <p role="alert">
          {errors[error] ?? "操作未完成，请刷新任务后检查状态。"}
        </p>
      )}
      <form
        className="collector-form"
        onSubmit={(event) => {
          event.preventDefault();
          void preview();
        }}
      >
        <label>
          财务来源
          <select
            value={source}
            disabled={busy || !canManage}
            onChange={(e) => {
              setSource(e.target.value);
              setPlan(null);
            }}
          >
            {catalog.map((c) => (
              <option key={c.source} value={c.source} disabled={!c.configured}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          财务证券代码
          <input
            value={instrument}
            required
            disabled={busy || !canManage}
            onChange={(e) => {
              setInstrument(e.target.value);
              setPlan(null);
            }}
          />
        </label>
        <label>
          报告期开始（含）
          <input
            type="date"
            value={start}
            required
            disabled={busy || !canManage}
            onChange={(e) => {
              setStart(e.target.value);
              setPlan(null);
            }}
          />
        </label>
        <label>
          报告期结束（不含）
          <input
            type="date"
            value={end}
            required
            disabled={busy || !canManage}
            onChange={(e) => {
              setEnd(e.target.value);
              setPlan(null);
            }}
          />
        </label>
        <p>
          日期轴：报告截止日；市场时区：北京时间。请明确选择范围，本操作重新采集所选范围，不承诺只补缺。
        </p>
        <button
          className="button"
          disabled={busy || !available || !canManage || !selected?.configured}
        >
          预览财务计划
        </button>
      </form>
      {selected && (
        <ul>
          {selected.limitations.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      )}
      {!canManage && <p>当前为只读权限，可浏览已发布财务数据。</p>}
      {plan && (
        <div className="panel research-plan-preview">
          <h3>财务采集计划</h3>
          <p>
            {plan.request.instrument_ids.length} 个标的 ·{" "}
            {plan.request.instrument_ids.join("、")} · {plan.request.start} 至{" "}
            {plan.request.end}（结束不含） · {plan.partitions.length} 个分段
          </p>
          <ResearchPlanControls
            plan={plan}
            name="财务"
            confirmed={confirmed}
            onConfirm={setConfirmed}
          />
          <button
            className="button button-primary"
            disabled={
              busy || !canManage || (plan.confirmation_required && !confirmed)
            }
            onClick={submit}
          >
            执行财务采集
          </button>
        </div>
      )}
      <ResearchJobHistory
        name="财务"
        dateAxis="报告期"
        jobs={jobs}
        instrumentNames={instrumentNames}
      >
        {(job) => (
          <tr key={job.request_id} data-research-job={job.request_id}>
            <td>
              {job.request.instrument_ids
                .map((id) => `${instrumentNames[id] ?? "名称未核验"} · ${id}`)
                .join("、")}
            </td>
            <td>
              {job.request.source}
              <br />
              {job.request.start} 至 {job.request.end}
            </td>
            <td>
              {states[job.state] ?? job.state}
              {job.error_code && (
                <p>
                  {job.wait_reason
                    ? budgetWaitMessage(
                        job.wait_reason,
                        job.state,
                        job.next_attempt_at ?? null,
                      )
                    : (errors[job.error_code] ?? job.error_code)}
                </p>
              )}
            </td>
            <td>
              <button
                className="button button-secondary"
                disabled={busy || !job.publication_id}
                onClick={() => browse(job)}
              >
                浏览财务版本
              </button>
              <ResearchReplayControl
                job={job.request_id}
                canManage={canManage}
                onSubmitted={refresh}
              />
              <ResearchProcessingFailures job={job.request_id} />
              <ResearchResponseReceipts
                key={job.request_id}
                job={job.request_id}
              />
              {canManage &&
                ["waiting", "failed", "no_observations"].includes(
                  job.state,
                ) && (
                  <button
                    className="button button-secondary"
                    disabled={busy}
                    onClick={() => void retry(job)}
                  >
                    {job.state === "waiting"
                      ? "提前重试预算等待"
                      : "重试当前任务"}
                  </button>
                )}
            </td>
          </tr>
        )}
      </ResearchJobHistory>
      {!jobs.length && <p>暂无财务采集任务。先选择报告期并预览计划。</p>}
      {version && (
        <div data-research-versions="financial_statement">
          <h3>已发布财务版本</h3>
          <p style={{ overflowWrap: "anywhere" }}>
            内容版本：{version} · 门禁：
            {quality === "warning" ? "通过，附语义警告" : "通过"}
          </p>
          <p>
            金额单位未确认时仅展示供应商原值，不进行单位换算。历史可知时间未知，不作为
            PIT 就绪数据。
          </p>
          {records.map((record, i) => (
            <details key={`${record.report_period}-${i}`}>
              <summary>
                {instrumentNames[record.instrument_id] ?? "名称未核验"} ·{" "}
                {record.instrument_id} · 报告期 {record.report_period} · 披露{" "}
                {record.disclosure_date ?? "未知"}
              </summary>
              <div style={{ overflowX: "auto", maxWidth: "100%" }}>
                <table>
                  <caption>供应商财务原值</caption>
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>原值</th>
                      <th>单位</th>
                    </tr>
                  </thead>
                  <tbody>
                    {record.metrics.map((m) => (
                      <tr key={m.field_id}>
                        <td>{m.name}</td>
                        <td>
                          {m.original_value ??
                            (m.missing_reason === "not_reported"
                              ? "未报告"
                              : "来源空值")}
                        </td>
                        <td>{m.original_unit.unit ?? "未知，未换算"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
