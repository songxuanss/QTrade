"use client";

import { useEffect, useRef, useState } from "react";
import {
  readFreshnessWatch,
  createFreshnessWatch,
  controlFreshnessWatch,
} from "../../app/workspace/freshness-watch-actions";
import styles from "./freshness-panel.module.css";

export type FreshnessWatch = {
  watch_id: string;
  revision: number;
  enabled: boolean;
  interval_seconds: number;
  actor_id: string;
  next_due_at: string;
  last_checked_at: string | null;
  paused_reason: string | null;
  last_error_code: string | null;
};
const issues: Record<string, string> = {
  forbidden: "执行主体已无管理权限，请有权限的人重新启用",
  unauthorized: "授权不可用，请核对登录和来源配置",
  unsupported: "当前类别的自动核验规则尚未接入",
  unavailable: "服务或来源暂不可用，请稍后核对",
  unverified: "归档证据未通过核验，请检查质量结果",
  conflict: "配置或运行资格已变化，请刷新状态",
  rate_limited: "预算或并发已达限制，请核对用量",
  not_found: "未找到可核验的发布",
  timeout: "来源请求超时，请稍后核对",
  invalid_input: "配置或证据不符合要求",
};
const issue = (value: string | null) =>
  value ? (issues[value] ?? "原因待核对，请查看运维记录") : "无已记录问题";
const date = (value: string | null) =>
  value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        dateStyle: "medium",
        timeStyle: "medium",
      }).format(new Date(value))
    : "尚未保存核验";

/** UI controls only configuration; current actor/scope and execution fencing
 * belong to the backend. An enabled timer is not evidence of healthy data. */
export function FreshnessWatchPanel({
  publication,
  canManage,
}: {
  publication: string;
  canManage: boolean;
}) {
  const [watch, setWatch] = useState<FreshnessWatch | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  async function run(action: "read" | "create" | "enable" | "disable") {
    const request = ++generation.current;
    setBusy(true);
    try {
      const result =
        action === "read"
          ? await readFreshnessWatch(publication)
          : action === "create"
            ? await createFreshnessWatch(publication)
            : watch
              ? await controlFreshnessWatch(
                  watch.watch_id,
                  watch.revision,
                  action === "enable",
                )
              : { error: "invalid_input" };
      if (request !== generation.current) return;
      if (result.watch) {
        setWatch(result.watch);
        setLoaded(true);
        setError("");
      } else if (result.error === "not_found" && action === "read") {
        setWatch(null);
        setLoaded(true);
        setError("");
      } else setError(result.error ?? "unavailable");
    } catch {
      if (request === generation.current) setError("unavailable");
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  return (
    <details className={styles.result} data-freshness-watch={publication}>
      <summary className={styles.watchSummary}>后台证据核验</summary>
      <section aria-label="后台证据核验">
        <h4>核验配置与执行记录</h4>
        <p className={styles.hint}>
          按同一标的、来源和日线口径核验已有发布；不会下载最新行情。启用后以当前操作者的有效权限运行，日历访问可能消耗元数据额度。
        </p>
        <button
          className="button button-secondary"
          disabled={busy}
          onClick={() => void run("read")}
        >
          {loaded ? "刷新后台核验状态" : "查看后台核验"}
        </button>
        {busy && <p>正在处理后台核验配置…</p>}
        {error && (
          <p role="alert">
            {error === "conflict"
              ? "配置已变化，请刷新状态后重试。"
              : error === "forbidden"
                ? "当前权限不允许此操作。"
                : "后台核验状态暂不可用，请稍后重试；旧记录仅供参考。"}
          </p>
        )}
        {loaded && !watch && (
          <p>
            尚未配置后台核验。
            {canManage && (
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => void run("create")}
              >
                配置每15分钟核验
              </button>
            )}
          </p>
        )}
        {watch && (
          <>
            <p>
              {error
                ? "状态待核验"
                : watch.paused_reason
                  ? "已暂停"
                  : watch.enabled
                    ? "已启用"
                    : "已停用"}{" "}
              · 每 {watch.interval_seconds} 秒核验
            </p>
            <dl className={styles.grid}>
              <div>
                <dt>最近保存核验（北京时间）</dt>
                <dd>{date(watch.last_checked_at)}</dd>
              </div>
              <div>
                <dt>下次应执行（北京时间）</dt>
                <dd>
                  {watch.enabled ? date(watch.next_due_at) : "停用期间不执行"}
                </dd>
              </div>
              <div>
                <dt>最近执行问题</dt>
                <dd>{issue(watch.last_error_code)}</dd>
              </div>
              <div>
                <dt>暂停原因</dt>
                <dd>{issue(watch.paused_reason)}</dd>
              </div>
            </dl>
            {canManage && (
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => void run(watch.enabled ? "disable" : "enable")}
              >
                {watch.enabled ? "停用后台核验" : "以我身份启用"}
              </button>
            )}
            <p className={styles.hint}>
              配置默认停用，需明确启用。停用保留已有核验和告警。后台状态与数据健康分开，查看上方新鲜度快照了解核验结果。
            </p>
          </>
        )}
      </section>
    </details>
  );
}
