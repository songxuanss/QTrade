"use client";

import styles from "./collector-scope.module.css";
import { scopeSummary, targetLabel } from "./collector-scope";
import type { CollectorSplitApi } from "./collector-panel";

/** Persisted, cross-view summary of the collection draft. Every segment jumps
 * back to the view and field it summarizes; it never replaces form validation
 * or the frozen preview contract. */
export function WorkbenchScopeBar({
  collector,
}: {
  collector: CollectorSplitApi;
}) {
  const summary = scopeSummary(collector.scope);
  return (
    <section
      className={styles.bar}
      aria-label="采集范围摘要"
      data-workbench-scope-bar="true"
    >
      <span className={styles.barTitle}>当前采集范围</span>
      <div className={styles.segments}>
        <button
          type="button"
          className={styles.segment}
          data-scope-segment="kind"
          onClick={() => collector.actions.focusField("kind")}
        >
          <span className={styles.label}>类别</span>
          <strong>
            {summary.kindLabel}
            {collector.scope.kind === "bars" && collector.scope.period
              ? ` · ${collector.scope.period}`
              : ""}
          </strong>
        </button>
        <button
          type="button"
          className={styles.segment}
          data-scope-segment="target"
          onClick={() =>
            collector.actions.focusField(
              summary.inherited ? "pools" : "instruments",
            )
          }
        >
          <span className={styles.label}>标的</span>
          <strong>{targetLabel(summary)}</strong>
          {summary.inherited && (
            <span className={styles.inherited} data-scope-inherited="true">
              继承自数据目录
            </span>
          )}
        </button>
        <button
          type="button"
          className={styles.segment}
          data-scope-segment="dates"
          onClick={() => collector.actions.focusField("dates")}
        >
          <span className={styles.label}>日期</span>
          <strong>{summary.dates.label}</strong>
        </button>
        <button
          type="button"
          className={styles.segment}
          data-scope-segment="source"
          onClick={() => collector.actions.focusField("source")}
        >
          <span className={styles.label}>来源</span>
          <strong>
            {summary.source.label}
            {summary.source.configured ? "" : " · 未配置"}
          </strong>
        </button>
        {collector.scope.kind === "factors" && (
          <button
            type="button"
            className={styles.segment}
            data-scope-segment="fields"
            onClick={() => collector.actions.focusField("fields")}
          >
            <span className={styles.label}>因子</span>
            <strong>
              {summary.fields.count ? `${summary.fields.count} 项` : "尚未选择"}
            </strong>
          </button>
        )}
      </div>
      <button
        type="button"
        className={styles.catalogLink}
        onClick={() => collector.actions.goToView("catalog")}
      >
        数据目录
      </button>
    </section>
  );
}

/** Narrow-screen draft summary: fixed above the viewport bottom so the selected
 * count and the list entry survive long form scrolling. */
export function MobileSelectionBar({
  collector,
}: {
  collector: CollectorSplitApi;
}) {
  const summary = scopeSummary(collector.scope);
  const hasDraft =
    summary.combined > 0 ||
    (collector.scope.kind === "factors" && summary.fields.count > 0);
  if (!hasDraft) return null;
  return (
    <div
      className={styles.mobileBar}
      role="region"
      aria-label="已选范围"
      data-mobile-scope-bar="true"
    >
      <span>
        已选 {summary.combined} 个标的
        {collector.scope.kind === "factors"
          ? ` · ${summary.fields.count} 个因子`
          : ""}
      </span>
      <button
        type="button"
        className="button button-secondary"
        onClick={() => collector.actions.openInstrumentDrawer()}
      >
        查看清单
      </button>
    </div>
  );
}
