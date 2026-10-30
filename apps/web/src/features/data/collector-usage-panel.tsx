"use client";

import { useEffect, useState } from "react";
import { readCollectorUsage } from "../../app/workspace/collector-actions";
import type { UsageSnapshot, UsageBudget } from "./collector-usage-types";
import styles from "./collector-usage-panel.module.css";

const reasons = {
  source_daily: "来源当日信用不足；等待预算重置，或由管理员调整配置。",
  scope_daily: "当前空间当日信用不足；等待预算重置，或由管理员调整配置。",
  source_concurrency: "来源预留已满；等待已占用的预留完成或到期后重试。",
  scope_concurrency: "当前空间预留已满；等待已占用的预留完成或到期后重试。",
};
const operations: Record<string, string> = {
  "bars.history": "历史行情",
  "factors.history": "历史因子",
  "research.data": "财务 / 事件",
  "metadata.calendar": "交易日历",
  "metadata.names": "证券名称",
  "metadata.factor_catalog": "因子目录",
};
const failures: Record<string, string> = {
  timeout: "超时",
  unavailable: "暂不可用",
  unauthorized: "来源授权失败",
  rate_limited: "来源限流",
  unverified: "响应未验证",
};
function dated(value: string, timezone: string) {
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: timezone,
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return "时间待确认";
  }
}
function Budget({ title, value }: { title: string; value: UsageBudget }) {
  return (
    <div className={styles.budget}>
      <h3>{title}</h3>
      <p>
        <strong>{value.credits_used.toLocaleString()}</strong> /{" "}
        {value.daily_limit.toLocaleString()} 信用
      </p>
      <progress
        aria-label={`${title}当日信用使用`}
        value={Math.min(value.credits_used, value.daily_limit)}
        max={value.daily_limit}
      />
      <small>
        剩余{" "}
        {Math.max(0, value.daily_limit - value.credits_used).toLocaleString()} ·
        有效预留 {value.active_reservations} / {value.concurrency_limit}
      </small>
    </div>
  );
}

export function CollectorUsagePanel() {
  const [snapshot, setSnapshot] = useState<UsageSnapshot | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [refreshed, setRefreshed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void readCollectorUsage()
      .catch(
        () =>
          ({ error: "unavailable" }) as {
            snapshot?: UsageSnapshot;
            error?: string;
          },
      )
      .then((result) => {
        if (cancelled) return;
        setSnapshot(result.snapshot ?? null);
        setError(result.error ?? "");
        setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  async function refresh() {
    setBusy(true);
    setRefreshed(false);
    try {
      const result = await readCollectorUsage();
      if (result.snapshot) setSnapshot(result.snapshot);
      setError(result.error ?? "");
      setRefreshed(Boolean(result.snapshot));
    } catch {
      // A browser/BFF transport failure must release the button while retaining
      // visibly stale evidence; never replace failed reads with invented zeros.
      setError("unavailable");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className={styles.panel}
      aria-label="采集用量"
      data-usage-scope={snapshot?.scope_id ?? ""}
      data-usage-generated={snapshot?.generated_at ?? ""}
    >
      <div className={styles.heading}>
        <div>
          <h2>采集用量</h2>
          <p>MaiRui · 内部信用预算，与供应商账单及订阅权益独立</p>
        </div>
        <button type="button" disabled={busy} onClick={() => void refresh()}>
          {busy ? "读取用量中…" : "刷新用量"}
        </button>
      </div>
      <p role="status">
        {error
          ? "用量暂不可查询，请重试；保留的快照不代表当前额度。"
          : refreshed
            ? "用量已更新"
            : "查询不会调用供应商，也不会扣信用。"}
      </p>
      {snapshot && (
        <>
          <div className={styles.budgets}>
            <Budget title="当前空间" value={snapshot.scope_budget} />
            <Budget title="共享来源" value={snapshot.source_budget} />
          </div>
          <p>
            {snapshot.day} · {snapshot.timezone} · 重置{" "}
            {dated(snapshot.resets_at, snapshot.timezone)} · 快照{" "}
            {dated(snapshot.generated_at, snapshot.timezone)}
          </p>
          <div className={styles.stats}>
            <span>
              已预留请求 <b>{snapshot.request_count}</b>
            </span>
            <span>
              成功 <b>{snapshot.success_count}</b>
            </span>
            <span>
              失败 <b>{snapshot.failure_count}</b>
            </span>
            <span>
              到期 <b>{snapshot.expired_count}</b>
            </span>
            <span>
              拒绝 <b>{snapshot.denial_count}</b>
            </span>
            <span>
              响应 <b>{(snapshot.response_bytes / 1024).toFixed(1)} KiB</b>
            </span>
          </div>
          <p>
            因子历史每次 5 信用，其他当前操作每次 1
            信用；失败与到期不退款，缓存命中不算新调用。有效预留不代表连接仍在运行。
          </p>
          <details>
            <summary>当前空间请求与拒绝明细（各最近 50 条）</summary>
            <div
              className={styles.scroll}
              tabIndex={0}
              aria-label="采集请求明细"
            >
              <table>
                <caption>当日已预留请求</caption>
                <thead>
                  <tr>
                    <th>操作 / 时间</th>
                    <th>信用</th>
                    <th>结果</th>
                    <th>耗时</th>
                    <th>字节</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshot.recent_requests.map((r) => (
                    <tr key={r.request_id}>
                      <td>
                        {operations[r.operation] ?? r.operation}
                        <small>
                          {dated(r.reserved_at, snapshot.timezone)} ·{" "}
                          <code>{r.request_id}</code>
                        </small>
                      </td>
                      <td>{r.credits}</td>
                      <td>
                        {r.outcome === "success"
                          ? "成功"
                          : r.outcome === "failure"
                            ? (failures[r.error_code ?? ""] ?? "失败")
                            : r.state === "expired" || r.lease_elapsed
                              ? "租约已到期"
                              : "预留中"}
                      </td>
                      <td>
                        {r.duration_ms === null ? "—" : `${r.duration_ms} ms`}
                      </td>
                      <td>{r.response_bytes ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!snapshot.recent_requests.length && <p>当日暂无已预留请求。</p>}
            </div>
            <ul className={styles.denials}>
              {snapshot.recent_denials.map((r) => (
                <li key={r.request_id}>
                  <strong>
                    {operations[r.operation] ?? r.operation} ·{" "}
                    {dated(r.denied_at, snapshot.timezone)}
                  </strong>
                  <p>{reasons[r.reason]}</p>
                  <code>{r.request_id}</code>
                </li>
              ))}
            </ul>
            {!snapshot.recent_denials.length && <p>当日暂无预算或预留拒绝。</p>}
            <p>
              仅显示当前空间明细。重试是新的出站尝试；队列等待、存储占用与其他采集路径尚未纳入此视图。
            </p>
          </details>
        </>
      )}
    </section>
  );
}
