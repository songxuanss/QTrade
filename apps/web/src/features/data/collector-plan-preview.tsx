"use client";

import { periods, states } from "./collector-labels";
import type {
  CollectorJob,
  CollectorPreview,
  CollectorSource,
} from "./collector-types";

/** Step 02 presentation: plan summary, guard/confirmation disclosure and the
 * recent-job shortcuts. Preview/submit state lives in CollectorPanel. */
export function CollectorPlanPreview({
  preview,
  catalog,
  planConfirmed,
  onPlanConfirmedChange,
  busy,
  onSubmit,
  jobs,
  onRefreshJob,
}: {
  preview: CollectorPreview | null;
  catalog: CollectorSource[];
  planConfirmed: boolean;
  onPlanConfirmedChange: (checked: boolean) => void;
  busy: boolean;
  onSubmit: () => void;
  jobs: CollectorJob[];
  onRefreshJob: (jobId: string) => void;
}) {
  return (
    <aside className="collector-sidebar" aria-label="计划预览与最近任务">
      {!preview && (
        <div className="collector-sidebar-empty">
          <h3>计划摘要</h3>
          选择数据范围后生成预览。确认新增分段和覆盖情况，再提交任务。
        </div>
      )}
      {preview && (
        <div className="collector-preview" aria-label="独立采集计划">
          <div className="collector-zone-heading compact">
            <div>
              <span className="collector-step-number">02</span>
              <div>
                <h3>
                  {preview.missing_parts === 0
                    ? "所选范围已有数据或任务"
                    : "下载计划已生成"}
                </h3>
                <p>核对范围和新增任务后确认提交</p>
              </div>
            </div>
          </div>
          <p>
            {Object.entries(preview.instrument_names)
              .slice(0, 8)
              .map(([code, name]) => `${name} · ${code}`)
              .join("，")}
            {preview.request.instrument_ids.length > 8
              ? ` 等 ${preview.request.instrument_ids.length} 个标的`
              : ""}
          </p>
          <p className="data-secondary">
            {catalog.find((item) => item.source === preview.request.source)
              ?.name ?? preview.request.source}
            {" · "}
            {periods[preview.request.granularity] ??
              preview.request.granularity}
            {" · UTC 半开范围："}
            {preview.request.start} — {preview.request.end}
          </p>
          {preview.field_definitions.length > 0 && (
            <p className="data-secondary">
              因子：
              {preview.field_definitions.map((field) => field.name).join("、")}
            </p>
          )}
          <div className="collector-metrics">
            <span>
              总分段 <strong>{preview.planned_parts}</strong>
            </span>
            <span>
              已完成 <strong>{preview.completed_parts}</strong>
            </span>
            <span>
              处理中 <strong>{preview.active_parts}</strong>
            </span>
            <span>
              新增下载 <strong>{preview.missing_parts}</strong>
            </span>
          </div>
          <p className="data-secondary">
            {preview.request.mode === "all"
              ? "将重新请求全部范围，并保留已有产物。"
              : "复用已完成或处理中的范围，只采集缺失部分。"}
          </p>
          {preview.execution_estimate && (
            <div aria-label="采集调用估计">
              <p>
                执行调用估计 {preview.execution_estimate.calls_min}–
                {preview.execution_estimate.calls_max} 次 · 内部信用{" "}
                {preview.execution_estimate.credits_min}–
                {preview.execution_estimate.credits_max}
              </p>
              <p className="data-secondary">
                {preview.execution_estimate.basis}
              </p>
            </div>
          )}
          {preview.plan_guard && (
            <p className="data-secondary">
              单次上限：{preview.plan_guard.max_instruments} 个标的 /{" "}
              {preview.plan_guard.max_new_parts}{" "}
              个新增分段。确认不能绕过硬预算和规模限制。
            </p>
          )}
          {preview.confirmation_required && (
            <label>
              <input
                type="checkbox"
                aria-label="确认计划调用规模"
                checked={planConfirmed}
                onChange={(event) =>
                  onPlanConfirmedChange(event.target.checked)
                }
              />
              我已核对新增任务及调用区间，确认执行；实际重试可能增加用量。
            </label>
          )}
          <button
            className="button button-primary"
            disabled={
              busy || Boolean(preview.confirmation_required && !planConfirmed)
            }
            onClick={onSubmit}
          >
            提交独立采集任务
          </button>
        </div>
      )}
      {jobs.length > 0 && (
        <details className="collector-recent">
          <summary>
            <span>最近任务</span>
            <span className="collector-history-count">{jobs.length}</span>
          </summary>
          <ul>
            {jobs.map((item) => (
              <li key={item.job_id}>
                <button
                  className="button button-quiet"
                  onClick={() => onRefreshJob(item.job_id)}
                >
                  {Object.values(item.instrument_names).slice(0, 2).join("、")}{" "}
                  · {item.request.granularity} ·{" "}
                  {states[item.state] ?? item.state}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </aside>
  );
}
