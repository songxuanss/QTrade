import Link from "next/link";
import { PermissionState } from "../../components/ui/states";
import { auditParams, type AuditFilters, type workspaceAudit } from "./server";
type Data = Awaited<ReturnType<typeof workspaceAudit>>;
const labels: Record<string, string> = {
  success: "成功",
  denied: "拒绝",
  failure: "失败",
};
export function AuditPanel({
  data,
  filters,
  allowed,
}: {
  data: Data | null;
  filters: AuditFilters;
  allowed: boolean;
}) {
  const next = auditParams(filters);
  if (data?.next_before) next.set("before", String(data.next_before));
  const exportFilters = { ...filters };
  delete exportFilters.before;
  return (
    <div className="workspace-page audit-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">GOVERNANCE / AUDIT</p>
          <h1>审计记录</h1>
          <p className="page-description">
            按工作区追踪操作、权限裁决与关联事件。记录只能追加，导出受权限控制。
          </p>
        </div>
        {allowed && (
          <Link
            className="button button-secondary"
            href={`/workspace/audit/export?${auditParams(exportFilters)}`}
          >
            导出 CSV
          </Link>
        )}
      </header>
      {!allowed ? (
        <section className="panel">
          <PermissionState description="只有工作区管理员与风险管理员可以查询和导出审计记录。" />
        </section>
      ) : (
        <>
          <form action="/workspace/audit" className="panel audit-filter-form">
            <label>
              开始时间（UTC）
              <input
                name="start"
                type="datetime-local"
                defaultValue={filters.start?.replace(/Z$/, " ").trim()}
                aria-label="审计开始时间"
              />
            </label>
            <label>
              结束时间（UTC）
              <input
                name="end"
                type="datetime-local"
                defaultValue={filters.end?.replace(/Z$/, " ").trim()}
                aria-label="审计结束时间"
              />
            </label>
            <label>
              操作
              <input
                name="action"
                defaultValue={filters.action}
                placeholder="例如 data.collection.create"
                aria-label="审计操作"
                maxLength={256}
              />
            </label>
            <label>
              结果
              <select
                name="result"
                defaultValue={filters.result ?? ""}
                aria-label="审计结果"
              >
                <option value="">全部</option>
                <option value="success">成功</option>
                <option value="denied">拒绝</option>
                <option value="failure">失败</option>
              </select>
            </label>
            <label>
              操作者 ID
              <input
                name="actor_id"
                defaultValue={filters.actor_id}
                aria-label="审计操作者 ID"
                placeholder="UUID"
              />
            </label>
            <label>
              对象 ID
              <input
                name="target_id"
                defaultValue={filters.target_id}
                aria-label="审计对象 ID"
                placeholder="UUID"
              />
            </label>
            <label>
              追踪 ID
              <input
                name="trace_id"
                defaultValue={filters.trace_id}
                aria-label="审计追踪 ID"
                placeholder="UUID"
              />
            </label>
            <div className="data-actions">
              <button className="button button-primary">查询审计</button>
              <Link className="button button-secondary" href="/workspace/audit">
                重置
              </Link>
            </div>
          </form>
          <p className="data-description">
            默认最近 7 天；单次查询最长 31 天，单次导出最多 5,000
            条。来源地址只展示网段，凭证与会话摘要不出现在报告中。
          </p>
          {data?.error ? (
            <p className="data-notice" role="alert">
              {data.error === "invalid"
                ? "筛选条件无效，请填写有效 ID 和 UTC 时间范围（最长 31 天）。"
                : "暂时无法读取审计服务，请刷新重试。"}
            </p>
          ) : data?.events.length ? (
            <section className="panel data-section" aria-label="审计事件">
              <div className="data-section-title">
                <h2>操作历史</h2>
                <span>本页 {data.events.length} 条</span>
              </div>
              <div className="audit-table-wrap">
                <table className="audit-table">
                  <thead>
                    <tr>
                      <th>时间（UTC）</th>
                      <th>操作与对象</th>
                      <th>结果</th>
                      <th>操作者</th>
                      <th>追踪与来源</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.events.map((event) => (
                      <tr key={event.event_id} data-audit-id={event.event_id}>
                        <td>
                          {event.occurred_at
                            .replace("T", " ")
                            .replace(/\+00:00$/, " UTC")}
                        </td>
                        <td>
                          <strong>{event.action}</strong>
                          <small>{event.details.target_id ?? "—"}</small>
                        </td>
                        <td>
                          <span className={`data-chip ${event.result}`}>
                            {labels[event.result] ?? event.result}
                          </span>
                        </td>
                        <td>{event.actor_id ?? "系统 / 匿名"}</td>
                        <td>
                          <Link
                            href={`/workspace/audit?${new URLSearchParams({ trace_id: event.trace_id, ...(filters.start ? { start: filters.start } : {}), ...(filters.end ? { end: filters.end } : {}) })}`}
                          >
                            {event.trace_id}
                          </Link>
                          <small>
                            {event.source} · {event.source_network ?? "—"}
                          </small>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : (
            <section className="panel data-section">
              <h2>没有匹配的审计记录</h2>
              <p className="page-description">
                调整筛选条件，或完成工作区操作后重新查询。
              </p>
            </section>
          )}
          {data?.next_before && (
            <Link
              className="button button-secondary"
              href={`/workspace/audit?${next}`}
            >
              查看更早记录
            </Link>
          )}
        </>
      )}
    </div>
  );
}
