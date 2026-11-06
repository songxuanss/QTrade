"use client";

import styles from "./collector-scope.module.css";
import {
  cardLabel,
  cardTargets,
  stateLabel,
  type OrchestrationCard,
} from "./collection-orchestration";

export type OrchestrationApi = {
  cards: OrchestrationCard[];
  confirmed: boolean;
  busy: boolean;
  error: string;
  add: () => void;
  remove: (dimensionId: string) => void;
  previewAll: () => void;
  submitAll: () => void;
  setConfirmed: (value: boolean) => void;
  poolNames: ReadonlyMap<string, string>;
};

/** Multi-dimension composition: cards hold the drafts, "循环预览" previews each
 * card in order and "顺序提交" submits the previewed cards sequentially. The
 * server contract is unchanged: every card still becomes an ordinary frozen
 * collection request. */
export function CollectionOrchestration({
  orchestration,
}: {
  orchestration: OrchestrationApi;
}) {
  const {
    cards,
    confirmed,
    busy,
    error,
    add,
    remove,
    previewAll,
    submitAll,
    setConfirmed,
    poolNames,
  } = orchestration;
  const previewable = cards.filter((card) =>
    ["draft", "previewed", "failed"].includes(card.state),
  ).length;
  const submittable = cards.filter(
    (card) => card.state === "previewed" && card.preview,
  ).length;
  const needsConfirmation = cards.some(
    (card) => card.state === "previewed" && card.preview?.confirmation_required,
  );
  return (
    <section
      className={styles.orchestration}
      aria-label="多维编排"
      data-orchestration="true"
    >
      <header className={styles.orchestrationHeader}>
        <div>
          <h2>多维编排</h2>
          <p>
            把当前的类别、来源、标的与日期加入编排；循环预览后按顺序逐个提交，每张卡片仍生成独立的冻结请求。
          </p>
        </div>
        <div className={styles.orchestrationActions}>
          <button
            type="button"
            className="button button-secondary"
            onClick={add}
            disabled={busy || cards.length >= 20}
            data-orchestration-action="add"
          >
            加入编排
          </button>
          <button
            type="button"
            className="button button-secondary"
            onClick={previewAll}
            disabled={busy || previewable === 0}
            data-orchestration-action="preview"
          >
            {busy
              ? "处理中…"
              : `循环预览${previewable ? `（${previewable}）` : ""}`}
          </button>
          <button
            type="button"
            className="button button-primary"
            onClick={submitAll}
            disabled={busy || submittable === 0}
            data-orchestration-action="submit"
          >
            {busy
              ? "处理中…"
              : `顺序提交${submittable ? `（${submittable}）` : ""}`}
          </button>
        </div>
      </header>
      {needsConfirmation && (
        <label className={styles.orchestrationConfirm}>
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            data-orchestration-action="confirm"
          />
          确认批量计划调用规模（至少一张卡片需要显式确认）
        </label>
      )}
      {error && (
        <p className="data-notice" role="alert">
          {error}
        </p>
      )}
      {cards.length === 0 ? (
        <p className={styles.orchestrationEmpty} role="status">
          尚未加入编排。先在表单里配置范围，再点击“加入编排”。
        </p>
      ) : (
        <ol className={styles.orchestrationList}>
          {cards.map((card, index) => (
            <li
              key={card.dimension_id}
              className={styles.orchestrationCard}
              data-orchestration-card={card.dimension_id}
              data-card-state={card.state}
              data-card-job={card.jobId ?? ""}
            >
              <div className={styles.orchestrationCardHead}>
                <span className={styles.orchestrationIndex}>
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div>
                  <strong>{cardLabel(card, poolNames)}</strong>
                  <p>{cardTargets(card)}</p>
                </div>
                <span
                  className={`data-chip ${styles.orchestrationState}`}
                  data-card-state-label={card.state}
                >
                  {stateLabel[card.state]}
                </span>
                <button
                  type="button"
                  className="button button-secondary"
                  aria-label={`移除编排卡片 ${index + 1}`}
                  onClick={() => remove(card.dimension_id)}
                  disabled={busy}
                >
                  移除
                </button>
              </div>
              {card.preview && (
                <p className={styles.orchestrationDetail}>
                  计划分段：{card.preview.planned_parts} · 已完成{" "}
                  {card.preview.completed_parts} · 缺口{" "}
                  {card.preview.missing_parts}
                  {card.preview.execution_estimate
                    ? ` · 预计调用 ${card.preview.execution_estimate.calls_min}–${card.preview.execution_estimate.calls_max} 次`
                    : ""}
                  {card.preview.confirmation_required
                    ? " · 需要显式确认调用规模"
                    : ""}
                </p>
              )}
              {card.error && <p role="alert">{card.error}</p>}
              {card.jobId && (
                <p className={styles.orchestrationDetail}>
                  任务已提交：{card.jobId}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
