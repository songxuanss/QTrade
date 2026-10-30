"use client";

import { useEffect, useRef, useState } from "react";
import {
  checkFreshness,
  readFreshness,
} from "../../app/workspace/freshness-actions";
import type { FreshnessView } from "./freshness-types";
import styles from "./freshness-panel.module.css";
import { FreshnessWatchPanel } from "./freshness-watch-panel";

const statuses = {
  current: "符合上次检查的预期",
  within_grace: "仍在更新宽限期",
  late: "已超过预期更新时间",
  unknown: "证据不足，状态未知",
  unavailable: "来源不可用",
};
const reasons: Record<string, string> = {
  published_current: "已发布证据覆盖该次检查的预期周期。",
  source_behind: "来源完整周期落后于预期。",
  collection_behind: "来源已有更新，本地尚未取得对应完整周期。",
  publication_behind: "已取得原始数据，但对应周期尚未发布。",
  source_unavailable: "来源连接或授权不可用，缓存不能证明当前可用。",
  rule_unknown: "该类别的更新规则尚未核验。",
  source_unknown: "尚无来源最新观测，无法确定滞后发生在哪个环节。",
};
const actions: Record<string, string> = {
  none: "核对研究所需的历史覆盖与质量。",
  check_source: "先核验来源最新数据与授权，再决定是否补采。",
  collect_missing: "核对采集范围，预览并执行补缺计划。",
  check_publication: "查看质量检查和发布结果，处理阻断后重试。",
  verify_rule: "核对来源说明，确认此类数据的更新规则。",
};
function time(value: string | undefined) {
  return value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        dateStyle: "medium",
        timeStyle: "medium",
      }).format(new Date(value))
    : "未验证";
}

/** Dataset identity comes from the scoped manifest at the API. Snapshot reads
 * stay separate from the manager command, which verifies saved price evidence.
 */
export function FreshnessPanel({
  publication,
  canManage,
}: {
  publication: string;
  canManage: boolean;
}) {
  const [view, setView] = useState<FreshnessView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const started = useRef(0);
  const generation = useRef(0);
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  useEffect(() => {
    if (!view) return;
    const id = setInterval(
      () =>
        setElapsed(Math.floor((performance.now() - started.current) / 1000)),
      1000,
    );
    return () => clearInterval(id);
  }, [view]);
  async function load(check = false) {
    const request = ++generation.current;
    setBusy(true);
    setChecking(check);
    try {
      const result = await (check
        ? checkFreshness(publication)
        : readFreshness(publication));
      if (generation.current !== request) return;
      if (result.error) setError(result.error);
      else if (result.view) {
        setError("");
        setView(result.view);
        started.current = performance.now();
        setElapsed(0);
      }
    } catch {
      if (generation.current === request) setError("unavailable");
    } finally {
      if (generation.current === request) setBusy(false);
    }
  }
  const expired =
    view && (view.stale || view.age_seconds + elapsed > view.max_age_seconds);
  return (
    <div className={styles.panel} data-freshness-publication={publication}>
      <button
        className="button button-secondary"
        disabled={busy}
        onClick={() => void load()}
      >
        {view ? "刷新新鲜度快照" : "查看新鲜度快照"}
      </button>
      {canManage && (
        <button
          className="button button-secondary"
          disabled={busy}
          onClick={() => void load(true)}
        >
          重新核验新鲜度
        </button>
      )}
      {canManage && (
        <p className={styles.hint}>
          重新核验会读取交易日历和已归档证据，可能消耗元数据调用额度；不会下载最新行情。需补采时请预览采集计划。
        </p>
      )}
      {busy && (
        <p role="status">
          {checking ? "正在核验发布时间、归档和质量证据…" : "正在读取检查记录…"}
        </p>
      )}
      {error && (
        <p role="alert">
          {error === "not_found"
            ? "暂无新鲜度检查记录；下载完成不代表已核验新鲜度。"
            : error === "unsupported"
              ? "该类别或周期的新鲜度规则尚未接入。"
              : error === "forbidden"
                ? "当前工作区无权执行此操作。"
                : error === "unverified"
                  ? "现有归档不足以证明完整周期，当前状态待核验；请核对任务范围与质量结果。"
                  : error === "rate_limited"
                    ? "核验达到调用预算或并发限制，请查看用量并稍后重试。"
                    : "检查记录暂不可用，当前状态待核验；请稍后重试。"}
        </p>
      )}
      {view && (
        <section aria-label="数据新鲜度检查" className={styles.result}>
          <h4>数据新鲜度检查</h4>
          <p
            role="status"
            className={`${styles.status} ${
              !error &&
              !view.watch_issue &&
              !expired &&
              !view.clock_skew &&
              view.snapshot.status === "current"
                ? styles.current
                : styles.attention
            }`}
          >
            {error
              ? "读取或核验失败，当前状态待核验"
              : view.watch_issue
                ? "最近后台核验未完成，当前状态待核验"
                : view.clock_skew
                  ? "检查时钟异常，当前状态待核验"
                  : expired
                    ? "检查记录已过期，当前状态待核验"
                    : statuses[view.snapshot.status]}
          </p>
          <p>
            上次检查：{reasons[view.snapshot.reason] ?? "检查证据尚不完整。"}
          </p>
          <dl className={styles.grid}>
            <div>
              <dt>检查有效时长</dt>
              <dd>{view.max_age_seconds} 秒</dd>
            </div>
            <div>
              <dt>来源更新宽限期</dt>
              <dd>
                {view.snapshot.expectation
                  ? `${view.snapshot.expectation.grace_seconds} 秒`
                  : "规则未知"}
              </dd>
            </div>
            <div>
              <dt>预期截至</dt>
              <dd>
                {view.snapshot.expectation?.complete_through ?? "规则未知"}
              </dd>
            </div>
            <div>
              <dt>来源完整周期</dt>
              <dd>
                {view.snapshot.evidence.source?.complete_through ?? "未验证"}
              </dd>
            </div>
            <div>
              <dt>本地已采集周期</dt>
              <dd>
                {view.snapshot.evidence.collected?.complete_through ?? "未验证"}
              </dd>
            </div>
            <div>
              <dt>已发布周期</dt>
              <dd>
                {view.snapshot.evidence.published?.complete_through ?? "未验证"}
              </dd>
            </div>
            <div>
              <dt>采集时间（北京时间）</dt>
              <dd>{time(view.snapshot.evidence.collected?.observed_at)}</dd>
            </div>
            <div>
              <dt>发布时间（北京时间）</dt>
              <dd>{time(view.snapshot.evidence.published?.observed_at)}</dd>
            </div>
            <div>
              <dt>检查时间（北京时间）</dt>
              <dd>{time(view.snapshot.checked_at)}</dd>
            </div>
            <div>
              <dt>预计可获取时间（北京时间）</dt>
              <dd>{time(view.snapshot.expectation?.available_at)}</dd>
            </div>
          </dl>
          <p>
            上次判定：{statuses[view.snapshot.status]}。下一步：
            {actions[view.snapshot.action]}
          </p>
          <p className={styles.hint}>
            这是当前标的、来源、日线口径的最近保存记录，可能汇总其他批次。
            刷新快照仅重新读取记录，不触发来源检查。完整日周期不代表全部历史覆盖或研究就绪。
          </p>
        </section>
      )}
      <FreshnessWatchPanel publication={publication} canManage={canManage} />
    </div>
  );
}
