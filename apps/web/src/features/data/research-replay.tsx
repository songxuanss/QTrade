"use client";

import { useId, useRef, useState } from "react";
import {
  previewResearchReplay,
  readResearchReplay,
  submitResearchReplay,
  readResearchReplayState,
} from "../../app/workspace/research-replay-actions";
import type {
  ResearchReplayPreview,
  ResearchReplayState,
} from "./research-replay-types";
import styles from "./research-replay.module.css";

const errors: Record<string, string> = {
  invalid_input: "归档为空或超过处理上限。请检查完整输入和服务端限制。",
  unverified: "RAW缺页、摘要或身份不一致，无法安全重处理。请先检查原任务证据。",
  unsupported: "当前未注册兼容转换器，请先部署对应转换版本。",
  forbidden: "当前权限已不足，请检查工作区角色。",
  conflict: "输入、转换版本或限制发生变化。请重新预览后提交。",
  not_found: "未取得重处理意图。普通采集任务没有这份记录。",
  unavailable:
    "重处理服务暂不可用。可重试读取；提交不确定时请使用同一预览重试，避免创建另一个任务。",
};
const dateAxes: Record<string, string> = {
  report_period: "报告期",
  observation_date: "观测日期",
  effective_date: "生效日期",
  event_date: "事件日期",
};
const states: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  completed: "已发布",
  failed: "失败",
  waiting: "等待执行",
  no_observations: "无观测",
};

/** A fresh request is allocated once per explicit preview. Ambiguous submit
 * retries retain that request/control pair, so timeouts cannot duplicate work.
 * Modal keeps the task table compact; closing it never cancels a queued job. */
export function ResearchReplayControl({
  job,
  canManage,
  onSubmitted,
}: {
  job: string;
  canManage: boolean;
  onSubmitted: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  const [value, setValue] = useState<ResearchReplayPreview | null>(null);
  const [status, setStatus] = useState<ResearchReplayState | null>(null);
  const [mode, setMode] = useState<"preview" | "lineage" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function load(lineage: boolean) {
    setBusy(true);
    setError("");
    setValue(null);
    setStatus(null);
    setMode(lineage ? "lineage" : "preview");
    try {
      const result = lineage
        ? await readResearchReplay(job)
        : await previewResearchReplay(job, crypto.randomUUID());
      if (result.error) setError(result.error);
      else setValue(result.value ?? null);
    } catch {
      setError("unavailable");
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    if (!value || mode !== "preview") return;
    setBusy(true);
    setError("");
    try {
      const result = await submitResearchReplay(
        value.parent_request_id,
        value.request_id,
        value.control_sha256,
      );
      if (result.error) setError(result.error);
      else {
        setStatus(result.value ?? null);
        await onSubmitted();
      }
    } catch {
      setError("unavailable");
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    if (!value) return;
    setBusy(true);
    setError("");
    try {
      const result = await readResearchReplayState(value.request_id);
      if (result.error) setError(result.error);
      else {
        setStatus(result.value ?? null);
        await onSubmitted();
      }
    } catch {
      setError("unavailable");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button
        type="button"
        className="button button-secondary"
        onClick={() => dialog.current?.showModal()}
      >
        {canManage ? "RAW 重处理" : "重处理血缘"}
      </button>
      <dialog ref={dialog} className={styles.dialog} aria-labelledby={title}>
        <div className={styles.header}>
          <h3 id={title}>研究 RAW 重处理</h3>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => dialog.current?.close()}
          >
            关闭
          </button>
        </div>
        <p>
          使用完整归档执行当前转换与发布门禁，不访问供应商。生成独立任务和发布，原任务、失败证据及旧版本保留；关闭窗口不会取消已提交任务。
        </p>
        <p>
          原始采集时间与未知历史可知时间保持原样，重处理不补充 PIT 证据。仅有
          HTTP 回执或缺页的 RAW 不能使用本操作。
        </p>
        <div className={styles.actions}>
          {canManage && (
            <button
              type="button"
              className="button button-secondary"
              disabled={busy || Boolean(status)}
              onClick={() => void load(false)}
            >
              预览当前 RAW
            </button>
          )}
          <button
            type="button"
            className="button button-secondary"
            disabled={busy}
            onClick={() => void load(true)}
          >
            读取当前任务血缘
          </button>
        </div>
        {busy && <p role="status">正在处理，请稍候…</p>}
        {error && <p role="alert">{errors[error] ?? errors.unavailable}</p>}
        {value && (
          <div
            className={styles.summary}
            data-replay-request={value.request_id}
          >
            <h4>
              {mode === "preview" ? "待确认的离线计划" : "已保存的重处理意图"}
            </h4>
            <dl>
              <dt>来源与范围</dt>
              <dd>
                {value.source} · {value.instrument_ids.join("、")}
                <br />
                {value.start} 至 {value.end}（结束不含，日期轴：
                {dateAxes[value.date_axis] ?? "待核对"}）
              </dd>
              <dt>归档输入</dt>
              <dd>
                {value.pages} 页 · {value.raw_rows} 条原始记录 · 供应商调用 0
                次；标准数量由门禁后确定
              </dd>
              <dt>转换版本</dt>
              <dd>
                {value.original_mapping} → {value.target_mapping}
              </dd>
              <dt>处理上限</dt>
              <dd>
                {value.limits.max_pages} 页 / {value.limits.max_rows} 行 /{" "}
                {value.limits.max_bytes} 字节（含解压检查）
              </dd>
              <dt>父任务</dt>
              <dd>{value.parent_request_id}</dd>
              <dt>新任务</dt>
              <dd>{value.request_id}</dd>
            </dl>
            <details>
              <summary>原始采集时间</summary>
              {value.original_collected_at.map((at) => (
                <p key={at}>
                  <time dateTime={at}>{at}</time>
                </p>
              ))}
            </details>
            {mode === "preview" && canManage && !status && (
              <button
                type="button"
                className="button button-primary"
                disabled={busy}
                onClick={() => void submit()}
              >
                确认创建重处理任务
              </button>
            )}
            {(status || mode === "lineage") && (
              <button
                type="button"
                className="button button-secondary"
                disabled={busy}
                onClick={() => void refresh()}
              >
                刷新处理结果
              </button>
            )}
            {status && (
              <p role="status" data-replay-state={status.state}>
                任务状态：{states[status.state] ?? "未知"}
                {status.error_code
                  ? ` · ${errors[status.error_code] ?? errors.unavailable}`
                  : ""}
                {status.publication_id
                  ? ` · 发布版本：${status.publication_id}`
                  : " · 尚无发布版本"}
              </p>
            )}
          </div>
        )}
      </dialog>
    </>
  );
}
