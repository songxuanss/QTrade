"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { readFreshnessHealth } from "../../app/workspace/freshness-health-actions";
import type { HealthEntry, HealthPage } from "./freshness-health-types";
import styles from "./freshness-health-panel.module.css";
const statuses: Record<string, string> = {
  current: "符合保存时预期",
  within_grace: "检查时在宽限内",
  late: "检查时已滞后",
  unknown: "证据不足",
  unavailable: "检查时来源不可用",
  unchecked: "尚未核验",
  expired: "检查已过期",
  check_failed: "最近核验未完成",
  clock_skew: "检查时钟异常",
  source_unconfigured: "来源或版本未注册",
};
const problems: Record<string, string> = {
  forbidden: "执行主体已无管理权限",
  unauthorized: "授权不可用",
  unsupported: "更新规则尚未接入",
  unavailable: "服务或来源暂不可用",
  unverified: "证据不足以证明完整周期",
  conflict: "配置或执行资格已变化",
  rate_limited: "预算或并发受限",
  not_found: "没有匹配的发布",
  timeout: "来源请求超时",
  invalid_input: "配置或证据不符合要求",
};
const time = (value: string | null | undefined) =>
  value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        dateStyle: "medium",
        timeStyle: "medium",
      }).format(new Date(value))
    : "未验证";
const state = (entry: HealthEntry, elapsed: number, maxAge: number) =>
  entry.age_seconds !== null &&
  entry.age_seconds + elapsed > maxAge &&
  ["current", "within_grace", "late", "unknown", "unavailable"].includes(
    entry.effective_status,
  )
    ? "expired"
    : entry.effective_status;
const consumers = {
  not_applicable: "无事件",
  not_claimed: "待消费端领取",
  pending: "等待领取",
  leased: "消费端处理中",
  delivered: "消费端已确认接收",
};
/** A read-only, bounded inventory. Retain old evidence on failure, but remove
 * any current-health styling. Local monotonic elapsed time ages saved checks. */
export function FreshnessHealthPanel() {
  const [page, setPage] = useState<HealthPage | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const [elapsed, setElapsed] = useState(0);
  const generation = useRef(0),
    started = useRef(0);
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  useEffect(() => {
    if (!page) return;
    const timer = setInterval(
      () =>
        setElapsed(Math.floor((performance.now() - started.current) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, [page]);
  async function load(next: (string | undefined)[]) {
    const request = ++generation.current;
    setBusy(true);
    try {
      const result = await readFreshnessHealth(next.at(-1));
      if (request !== generation.current) return;
      if (result.page) {
        setPage(result.page);
        setCursors(next);
        setElapsed(0);
        started.current = performance.now();
        setError("");
      } else setError(result.error ?? "unavailable");
    } catch {
      if (request === generation.current) setError("unavailable");
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  return (
    <details
      className={styles.panel}
      data-freshness-health
      onToggle={(event) => {
        if (event.currentTarget.open && !page && !busy) void load([undefined]);
      }}
    >
      <summary className={styles.summary}>
        <h2 className={styles.title}>新鲜度健康</h2>
      </summary>
      <div className={styles.header}>
        <div>
          <p>本工作区已有核验记录或后台配置的数据</p>
          <p className={styles.hint}>
            未列出的数据并未因此验证。这里只读保存的证据，不探测来源或创建下载。
          </p>
        </div>
        <button
          className="button button-secondary"
          disabled={busy}
          onClick={() => void load([undefined])}
        >
          刷新健康快照
        </button>
      </div>
      {busy && <p>正在读取健康快照…</p>}
      {error && (
        <p role="alert">
          {error === "forbidden"
            ? "当前工作区无权读取健康记录。"
            : "健康快照读取失败，当前状态待核验；保留的旧记录仅供参考。"}
        </p>
      )}
      {page && (
        <>
          <p role="status">
            {error
              ? "读取失败，当前状态待核验"
              : `健康快照已更新 · 第 ${cursors.length} 页 · 本页 ${page.entries.length} 项`}
          </p>
          <p className={styles.hint}>
            读取时间（北京时间）：{time(page.read_at)} · 检查有效时长{" "}
            {page.max_age_seconds} 秒。来源已配置不等于授权或连接已验证。
          </p>
          {page.entries.length === 0 && (
            <p>暂无保存的核验或后台配置；这不代表全部数据健康。</p>
          )}
          <div
            className={styles.cards}
            role="region"
            aria-label="新鲜度记录"
            tabIndex={0}
          >
            {page.entries.map((entry) => {
              const status = state(entry, elapsed, page.max_age_seconds),
                snapshot = entry.snapshot,
                watch = entry.watch;
              return (
                <article
                  key={entry.key_sha256}
                  className={styles.card}
                  data-health-key={entry.key_sha256}
                >
                  <div className={styles.top}>
                    <div>
                      <h3>{entry.instrument_name}</h3>
                      <span>{entry.key.instrument_id}</span>
                    </div>
                    <span
                      className={`${styles.badge} ${!error && status === "current" ? styles.current : ""}`}
                    >
                      {error
                        ? "读取失败，待核验"
                        : (statuses[status] ?? "待核验")}
                    </span>
                  </div>
                  <p className={styles.hint}>
                    {entry.key.source} ·{" "}
                    {entry.key.dataset_kind === "bars"
                      ? "行情"
                      : entry.key.dataset_kind === "factors"
                        ? "因子"
                        : "研究数据"}{" "}
                    · {entry.key.period ?? entry.key.field ?? "按类别"}
                  </p>
                  <dl className={styles.grid}>
                    <div>
                      <dt>已发布截至</dt>
                      <dd>
                        {snapshot?.evidence.published?.complete_through ??
                          "未验证"}
                      </dd>
                    </div>
                    <div>
                      <dt>预期截至</dt>
                      <dd>
                        {snapshot?.expectation?.complete_through ?? "规则未知"}
                      </dd>
                    </div>
                    <div>
                      <dt>来源完整周期</dt>
                      <dd>
                        {snapshot?.evidence.source?.complete_through ??
                          "未验证"}
                      </dd>
                    </div>
                    <div>
                      <dt>最近保存检查</dt>
                      <dd>{time(snapshot?.checked_at)}</dd>
                    </div>
                    <div>
                      <dt>后台配置</dt>
                      <dd>
                        {!watch
                          ? "未配置"
                          : watch.paused_reason
                            ? "已暂停"
                            : watch.enabled
                              ? "配置已启用"
                              : "已停用"}
                      </dd>
                    </div>
                    <div>
                      <dt>最近后台尝试</dt>
                      <dd>{time(watch?.last_attempt_at)}</dd>
                    </div>
                    <div>
                      <dt>最近执行问题</dt>
                      <dd>
                        {watch?.last_error_code
                          ? (problems[watch.last_error_code] ?? "原因待核对")
                          : "无已记录问题"}
                      </dd>
                    </div>
                    <div>
                      <dt>事件接收</dt>
                      <dd>
                        {entry.consumer_lease_expired
                          ? "租约到期，等待重领"
                          : consumers[entry.consumer_state]}
                      </dd>
                    </div>
                  </dl>
                  <details className={styles.evidence}>
                    <summary>查看口径与时间证据</summary>
                    <p>
                      映射版本：{entry.key.mapping_version} · 调整口径：
                      {entry.key.adjustment === "unadjusted"
                        ? "未复权"
                        : (entry.key.adjustment ?? "不适用")}
                      {entry.key.field ? ` · 字段：${entry.key.field}` : ""}
                    </p>
                    <p>
                      来源配置：
                      {entry.source_configuration === "configured"
                        ? "已配置"
                        : entry.source_configuration === "unconfigured"
                          ? "未配置"
                          : "来源或版本未注册"}
                      。采集时间：
                      {time(snapshot?.evidence.collected?.observed_at)}
                      ；发布时间：
                      {time(snapshot?.evidence.published?.observed_at)}
                      ；预计可获取：{time(snapshot?.expectation?.available_at)}
                      。
                    </p>
                    {entry.last_notice && (
                      <p>
                        最近
                        {entry.last_notice.kind === "opened" ? "异常" : "恢复"}
                        事件：{entry.last_notice.notice_id} ·{" "}
                        {time(entry.last_notice.occurred_at)}
                      </p>
                    )}
                    <p>
                      消费端确认仅表示持久接收，不表示最终通知送达。
                      {entry.last_notice && (
                        <Link href="/workspace/alerts">查看告警中心</Link>
                      )}
                    </p>
                  </details>
                </article>
              );
            })}
          </div>
          <div className={styles.actions}>
            <button
              className="button button-secondary"
              disabled={busy || cursors.length <= 1}
              onClick={() => void load(cursors.slice(0, -1))}
            >
              上一页健康记录
            </button>
            <button
              className="button button-secondary"
              disabled={busy || !page.next_key}
              onClick={() =>
                page.next_key && void load([...cursors, page.next_key])
              }
            >
              下一页健康记录
            </button>
            <span className={styles.hint}>
              每页最多20项，不将本页统计当作全工作区覆盖。
            </span>
          </div>
        </>
      )}
    </details>
  );
}
