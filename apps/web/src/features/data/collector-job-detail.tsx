"use client";

import Link from "next/link";
import workspaceStyles from "./collector-workspace.module.css";
import { CollectionReplayControl } from "./collection-replay";
import { CollectionQualityPanel } from "./collection-quality-panel";
import { FreshnessPanel } from "./freshness-panel";
import { budgetWaits, errors, periods, states } from "./collector-labels";
import type {
  CollectorJob,
  CollectorPublication,
  CollectorRecord,
} from "./collector-types";

/** Steps 03/04 presentation: saved-task picker, run detail with part-level
 * evidence and controls, and the published-record browser. Polling, job
 * selection races and control actions stay in CollectorPanel. */
export function CollectorJobDetail({
  job,
  jobs,
  canManage,
  busy,
  jobReadError,
  pending,
  lastJobUpdate,
  onRefresh,
  onControl,
  publication,
  records,
  nextRecordOffset,
  onBrowse,
  onClosePublication,
}: {
  job: CollectorJob | null;
  jobs: CollectorJob[];
  canManage: boolean;
  busy: boolean;
  jobReadError: string;
  pending: boolean;
  lastJobUpdate: number | null;
  onRefresh: (jobId: string, offset?: number) => void;
  onControl: (action: "retry" | "cancel") => void;
  publication: CollectorPublication | null;
  records: CollectorRecord[];
  nextRecordOffset: number | null;
  onBrowse: (publication: CollectorPublication, offset?: number) => void;
  onClosePublication: () => void;
}) {
  return (
    <>
      <div className={workspaceStyles.taskPicker}>
        <label>
          查看已保存任务
          <select
            aria-label="查看已保存采集任务"
            value={job?.job_id ?? ""}
            onChange={(event) => {
              if (event.target.value) onRefresh(event.target.value);
            }}
          >
            <option value="" disabled>
              请选择任务
            </option>
            {job && !jobs.some((item) => item.job_id === job.job_id) && (
              <option value={job.job_id}>
                当前任务 · {job.job_id.slice(0, 8)}
              </option>
            )}
            {jobs.map((item) => (
              <option key={item.job_id} value={item.job_id}>
                {Object.values(item.instrument_names).slice(0, 2).join("、") ||
                  item.job_id.slice(0, 8)}
                {" · "}
                {periods[item.request.granularity] ?? item.request.granularity}
                {" · "}
                {states[item.state] ?? item.state}
                {" · "}
                {item.job_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <p>选择任务查看分段、质量和已发布记录，刷新后仍可继续查看。</p>
      </div>
      {!job && (
        <p className="data-footnote" role="status">
          {jobs.length ? "请选择一个已保存任务。" : "当前工作区暂无采集任务。"}
          {!canManage && " 当前为只读访问，可以查看进度和产物。"}
        </p>
      )}
      <div className={workspaceStyles.resultGrid}>
        {job && (
          <div className="collector-run" data-collector-job={job.job_id}>
            <div className="data-section-heading">
              <div className="collector-zone-heading compact">
                <div>
                  <span className="collector-step-number">03</span>
                  <div>
                    <h3>任务执行</h3>
                    <p>
                      {states[job.state] ?? job.state} · {job.total_parts}{" "}
                      个分段
                    </p>
                  </div>
                </div>
              </div>
              <button
                className="button button-secondary"
                onClick={() => onRefresh(job.job_id, job.offset)}
              >
                刷新采集进度
              </button>
            </div>
            <p
              className="data-footnote"
              role={jobReadError ? "alert" : "status"}
            >
              {jobReadError
                ? `${errors[jobReadError] ?? "进度读取失败。"} 当前显示上次成功快照；${pending ? "自动刷新将退避重试，也可" : "可"}点击“刷新采集进度”重试。`
                : pending
                  ? "自动刷新已开启，失败后将退避重试。"
                  : ""}
              {lastJobUpdate !== null &&
                ` 上次更新：${new Intl.DateTimeFormat("zh-CN", {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                  hour12: false,
                }).format(lastJobUpdate)}。`}
            </p>
            <p>
              {Object.entries(job.instrument_names)
                .slice(0, 6)
                .map(([code, name]) => `${name} · ${code}`)
                .join("，")}{" "}
              · {periods[job.request.granularity] ?? job.request.granularity}
            </p>
            <p className="data-secondary">
              关联 ID：<code>{job.request.correlation_id}</code> ·{" "}
              {job.request.source}
            </p>
            <div className="collector-metrics">
              <span>
                总分段 <strong>{job.total_parts}</strong>
              </span>
              <span>
                采集完成 <strong>{job.counts.completed ?? 0}</strong>
              </span>
              <span>
                失败 <strong>{job.counts.failed ?? 0}</strong>
              </span>
              <span>
                无观测 <strong>{job.counts.no_data ?? 0}</strong>
              </span>
            </div>
            {canManage && (
              <div className="data-actions">
                {["running", "waiting"].includes(job.state) && (
                  <button
                    className="button button-secondary"
                    disabled={busy}
                    onClick={() => onControl("cancel")}
                  >
                    取消独立任务
                  </button>
                )}
                {[
                  "failed",
                  "no_data",
                  "completed_with_gaps",
                  "cancelled",
                ].includes(job.state) && (
                  <button
                    className="button"
                    disabled={busy}
                    onClick={() => onControl("retry")}
                  >
                    重试失败或空分段
                  </button>
                )}
                {job.parts.some(
                  (p) => p.state === "waiting" && p.wait_reason,
                ) && (
                  <button
                    className="button"
                    disabled={busy}
                    onClick={() => onControl("retry")}
                  >
                    提前重试预算等待
                  </button>
                )}
              </div>
            )}
            <div
              className={`collector-parts ${workspaceStyles.parts}`}
              role="region"
              aria-label="采集任务分段"
              tabIndex={0}
            >
              {job.parts.map((part) => (
                <article
                  key={part.part_id}
                  className="collector-part"
                  data-collector-part={part.part_id}
                >
                  <div>
                    <strong>
                      {part.instrument_name}{" "}
                      <small>
                        {part.instrument_id}
                        {part.field
                          ? ` · ${job.field_definitions.find((f) => f.factor_id === part.field)?.name ?? part.field}`
                          : ""}
                      </small>
                    </strong>
                    <span className={`data-status status-${part.state}`}>
                      {part.wait_reason && part.state === "waiting"
                        ? "等待预算 / 预留"
                        : (states[part.state] ?? part.state)}
                    </span>
                  </div>
                  <p className="data-secondary">
                    {part.start.slice(0, 10)} — {part.end.slice(0, 10)} UTC ·
                    尝试 {part.attempt} 次
                  </p>
                  {part.error_code && (
                    <p role="status">
                      {part.wait_reason
                        ? part.state === "waiting"
                          ? budgetWaits[part.wait_reason]
                          : "预算拒绝，当前没有自动重试时间；请核对额度配置后手动重试。"
                        : (errors[part.error_code] ?? part.error_code)}
                      {part.state === "waiting"
                        ? ` 下次重试：${part.next_attempt_at}`
                        : ""}
                    </p>
                  )}
                  {part.execution_kind === "normalization_replay" && (
                    <p className="data-footnote">
                      离线重处理 · 原始获取时间保持，未重新访问供应商。
                    </p>
                  )}
                  <CollectionQualityPanel
                    job={job.job_id}
                    part={part.part_id}
                  />
                  <CollectionReplayControl
                    key={`${job.job_id}:${part.part_id}`}
                    job={job.job_id}
                    part={part.part_id}
                    canManage={canManage}
                    offline={part.execution_kind === "normalization_replay"}
                  />
                  {part.publication && (
                    <>
                      <p>
                        {part.publication.record_count.toLocaleString()}{" "}
                        条标准记录 ·{" "}
                        {part.publication.raw_record_count.toLocaleString()}{" "}
                        条原始记录
                      </p>
                      {part.publication.evidence?.opening_boundary_semantics ===
                        "unverified" && (
                        <p role="status" className="data-footnote">
                          1 分钟来源含额外09:30观测，既有标签转换后为09:29；
                          时段语义未确认，不能据此认证为常规一分钟区间。
                        </p>
                      )}
                      {part.publication.dataset_kind === "bars" &&
                        part.publication.granularity === "1d" && (
                          <FreshnessPanel
                            publication={part.publication.publication_id}
                            canManage={canManage}
                          />
                        )}
                      <p className="data-secondary">
                        归档 SHA-256：
                        {part.publication.raw_sha256.slice(0, 16)}…
                      </p>
                      <div className="data-actions">
                        <button
                          className="button button-secondary"
                          onClick={() => onBrowse(part.publication!)}
                        >
                          {part.publication.dataset_kind === "factors"
                            ? "查看因子数据"
                            : "查看行情记录"}
                        </button>
                        {part.publication.dataset_kind === "bars" &&
                          (part.consumer?.state === "visible" ? (
                            <Link
                              className="button"
                              href={`/workspace/market?instrument=${encodeURIComponent(part.instrument_id)}&granularity=${job.request.granularity}${part.consumer.dataset_id ? `&dataset_id=${encodeURIComponent(part.consumer.dataset_id)}` : ""}`}
                            >
                              终端可见 · 查看图表
                            </Link>
                          ) : (
                            <span
                              className={`data-notice ${part.consumer?.state === "failed" ? "" : "warning"}`}
                            >
                              {part.consumer?.state === "failed"
                                ? `市场导入失败 · ${part.consumer.error_code}，后台继续重试`
                                : "采集完成 · 等待市场导入"}
                            </span>
                          ))}
                      </div>
                    </>
                  )}
                </article>
              ))}
            </div>
            <nav className="universe-pagination" aria-label="独立采集分段分页">
              {job.offset > 0 && (
                <button
                  className="button"
                  onClick={() =>
                    onRefresh(job.job_id, Math.max(0, job.offset - 100))
                  }
                >
                  上一页分段
                </button>
              )}
              {job.next_offset !== null && (
                <button
                  className="button"
                  onClick={() => onRefresh(job.job_id, job.next_offset!)}
                >
                  下一页分段
                </button>
              )}
            </nav>
          </div>
        )}
        {publication && (
          <div className="collector-records" aria-label="采集数据预览">
            <div className="collector-zone-heading compact">
              <div>
                <span className="collector-step-number">04</span>
                <div>
                  <h3>
                    {publication.instrument_name} · {publication.instrument_id}{" "}
                    · {publication.field ?? periods[publication.granularity]}
                  </h3>
                  <p>已发布记录样本</p>
                </div>
              </div>
            </div>
            <p className="page-description">
              {publication.limitations.join(" ")}
            </p>
            <div
              className={`collector-table-scroll ${workspaceStyles.records}`}
              tabIndex={0}
              aria-label="采集记录表格"
            >
              <table>
                <thead>
                  <tr>
                    <th>日期 / 时间</th>
                    <th>
                      {publication.dataset_kind === "factors"
                        ? "因子值"
                        : "收盘价"}
                    </th>
                    <th>
                      {publication.dataset_kind === "factors"
                        ? "值状态 / 计算版本"
                        : "成交量（股）"}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {records.map((record, index) => (
                    <tr key={index}>
                      <td>{record.trade_date ?? record.timestamp}</td>
                      <td>
                        {publication.dataset_kind === "factors"
                          ? (record.value ?? "空值")
                          : record.close}
                      </td>
                      <td>
                        {publication.dataset_kind === "factors"
                          ? `${record.value_kind === "missing" ? (record.missing_reason === "not_available" ? "来源未开放" : "来源空值") : "来源观测"} / ${record.calc_version}`
                          : record.volume}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {nextRecordOffset !== null && (
              <button
                className="button"
                onClick={() => onBrowse(publication, nextRecordOffset)}
              >
                下一页记录
              </button>
            )}
            <button
              className="button button-quiet"
              onClick={onClosePublication}
            >
              关闭数据预览
            </button>
          </div>
        )}
      </div>
    </>
  );
}
