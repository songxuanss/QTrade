import Link from "next/link";
import { AlertRules } from "./rules";
import type { AlertRule } from "./rules-server";
import type { AlertDispositionRow, WorkspaceAlert } from "../auth/server";
import { EmptyState } from "../../components/ui/states";
import {
  acknowledgeAlert,
  claimAlert,
  closeAlert,
  noteAlert,
  silenceAlert,
} from "../../app/workspace/alert-actions";

const date = (value: string) =>
  new Date(value).toLocaleString("zh-CN", { hour12: false });
const severityName = { info: "提示", warning: "警告", critical: "严重" };
const statusName = { open: "待处理", acknowledged: "已确认", closed: "已关闭" };

export function AlertCenter({
  events,
  selected,
  history,
  canHandle,
  error,
  result,
  rules,
  rulesError,
  canWriteRules,
  rulesResult,
}: {
  events: WorkspaceAlert[];
  selected: WorkspaceAlert | null;
  history: AlertDispositionRow[];
  canHandle: boolean;
  error: boolean;
  result?: string;
  rules: AlertRule[];
  rulesError: boolean;
  canWriteRules: boolean;
  rulesResult?: string;
}) {
  return (
    <div className="workspace-page alert-center-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">OPERATIONS / MONITORING</p>
          <h1>告警中心</h1>
          <p className="page-description">
            统一查看市场、风险、数据与系统事件，并记录每次处置。
          </p>
        </div>
        <div className="alert-count">
          <strong>{events.filter((e) => e.status !== "closed").length}</strong>
          <span>未关闭告警</span>
        </div>
      </div>
      <AlertRules
        rules={rules}
        error={rulesError}
        canWrite={canWriteRules}
        result={rulesResult}
      />
      {result && (
        <p
          className={`alert-flash ${result === "success" ? "success" : "error"}`}
          role="status"
        >
          {result === "success"
            ? "处置已记录"
            : "操作失败，请检查权限或告警状态"}
        </p>
      )}
      {error ? (
        <section className="panel">
          <EmptyState
            title="告警暂不可用"
            description="无法载入告警列表，请稍后重试。"
          />
        </section>
      ) : events.length === 0 ? (
        <section className="panel">
          <EmptyState
            title="暂无告警"
            headingLevel={2}
            description="新的市场、风险和系统事件会显示在这里。"
          />
        </section>
      ) : (
        <div className="alert-center-grid">
          <section className="panel alert-list-panel" aria-label="告警列表">
            <div className="alert-list-heading">
              <h2>事件流</h2>
              <span>{events.length} 条</span>
            </div>
            <div className="alert-list">
              {events.map((event) => (
                <Link
                  key={event.event_id}
                  href={`/workspace/alerts?event_id=${event.event_id}`}
                  className={`alert-row ${selected?.event_id === event.event_id ? "selected" : ""}`}
                >
                  <span className={`alert-severity ${event.severity}`}>
                    {severityName[event.severity]}
                  </span>
                  <span className="alert-row-main">
                    <strong>{event.title}</strong>
                    <span>
                      {event.source.toUpperCase()} · {date(event.occurred_at)}
                    </span>
                  </span>
                  <span className={`alert-status ${event.status}`}>
                    {statusName[event.status]}
                  </span>
                </Link>
              ))}
            </div>
          </section>
          {selected && (
            <section className="panel alert-detail-panel" aria-label="告警详情">
              <div className="alert-detail-top">
                <div>
                  <p className="eyebrow">
                    {selected.source.toUpperCase()} EVENT
                  </p>
                  <h2>{selected.title}</h2>
                  <p>{selected.message}</p>
                </div>
                <span className={`alert-severity ${selected.severity}`}>
                  {severityName[selected.severity]}
                </span>
              </div>
              <div className="alert-facts">
                <div>
                  <span>状态</span>
                  <strong>{statusName[selected.status]}</strong>
                </div>
                <div>
                  <span>发生时间</span>
                  <strong>{date(selected.occurred_at)}</strong>
                </div>
                <div>
                  <span>认领人</span>
                  <strong>
                    {selected.assignee_id
                      ? selected.assignee_id.slice(0, 8)
                      : "未认领"}
                  </strong>
                </div>
                <div>
                  <span>资源</span>
                  <strong>{selected.resource_id ?? "—"}</strong>
                </div>
                {selected.silenced_until && (
                  <div>
                    <span>静默至</span>
                    <strong>{date(selected.silenced_until)}</strong>
                  </div>
                )}
              </div>
              {Object.keys(selected.context_snapshot).length > 0 && (
                <details className="alert-context">
                  <summary>触发时上下文</summary>
                  <pre>
                    {JSON.stringify(selected.context_snapshot, null, 2)}
                  </pre>
                </details>
              )}
              {canHandle && selected.status !== "closed" && (
                <div className="alert-actions">
                  <div className="alert-action-row">
                    <form action={claimAlert}>
                      <input
                        type="hidden"
                        name="event_id"
                        value={selected.event_id}
                      />
                      <button className="button button-secondary" type="submit">
                        认领
                      </button>
                    </form>
                    <form action={acknowledgeAlert}>
                      <input
                        type="hidden"
                        name="event_id"
                        value={selected.event_id}
                      />
                      <button className="button button-secondary" type="submit">
                        确认告警
                      </button>
                    </form>
                    <form action={silenceAlert}>
                      <input
                        type="hidden"
                        name="event_id"
                        value={selected.event_id}
                      />
                      <label>
                        静默时长
                        <select name="silence_minutes" defaultValue="60">
                          <option value="15">15 分钟</option>
                          <option value="60">1 小时</option>
                          <option value="360">6 小时</option>
                          <option value="1440">24 小时</option>
                        </select>
                      </label>
                      <button className="button button-secondary" type="submit">
                        静默
                      </button>
                    </form>
                    <form action={closeAlert}>
                      <input
                        type="hidden"
                        name="event_id"
                        value={selected.event_id}
                      />
                      <button className="button button-danger" type="submit">
                        关闭告警
                      </button>
                    </form>
                  </div>
                  <form className="alert-note-form" action={noteAlert}>
                    <input
                      type="hidden"
                      name="event_id"
                      value={selected.event_id}
                    />
                    <label htmlFor="alert-note">处置备注</label>
                    <div>
                      <input
                        id="alert-note"
                        name="note"
                        required
                        maxLength={2048}
                        placeholder="记录排查结论或交接信息"
                      />
                      <button className="button button-secondary" type="submit">
                        添加备注
                      </button>
                    </div>
                  </form>
                </div>
              )}
              <div className="alert-history">
                <div className="alert-history-heading">
                  <h3>处置历史</h3>
                  <span>{history.length} 条记录</span>
                </div>
                {history.length ? (
                  <ol>
                    {history.map((item, index) => (
                      <li key={`${item.occurred_at}-${index}`}>
                        <span className="history-dot" />
                        <div>
                          <strong>
                            {(
                              {
                                claim: "认领",
                                acknowledge: "确认",
                                silence: "静默",
                                close: "关闭",
                                note: "备注",
                              } as Record<string, string>
                            )[item.action] ?? item.action}
                          </strong>
                          {item.note && <p>{item.note}</p>}
                          <small>
                            {date(item.occurred_at)} ·{" "}
                            {item.actor_id.slice(0, 8)}
                          </small>
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="alert-history-empty">尚无处置记录</p>
                )}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
