"use client";

import { useId, useRef, useState } from "react";
import {
  listCollectionInputs,
  previewCollectionReplay,
  readCollectionReplay,
  submitCollectionReplay,
  readCollectionReplayState,
} from "../../app/workspace/collection-replay-actions";
import type {
  CollectionInputChoice,
  CollectionReplayView,
} from "./collection-replay-types";
import styles from "./research-replay.module.css";

const errors: Record<string, string> = {
  not_found:
    "未取得完整输入或离线意图。旧任务不一定保存了转换检查点，请检查原任务。",
  forbidden: "当前无管理权限；可查看已保存血缘。",
  unsupported: "当前未注册兼容的本地转换器，请先检查服务端部署。",
  invalid_input:
    "输入或回执超过处理上限，或选择无效。请检查完整输入与服务端限制。",
  unverified: "输入、回执或转换校验未通过。原始证据保留，未绕过门禁。",
  conflict: "输入、转换或规则发生变化，请重新预览。",
  unavailable:
    "服务暂不可用。提交结果不确定时，请使用同一预览重试，避免新建另一任务。",
};
const states: Record<string, string> = {
  running: "排队 / 处理中",
  waiting: "等待执行",
  completed: "已发布",
  failed: "失败",
  no_data: "无观测",
  cancelled: "已取消",
  completed_with_gaps: "部分无观测",
};

/** A candidate identity belongs to one explicit preview. Closing preserves it,
 * so an uncertain submission can be retried idempotently without re-downloading.
 * Native result navigation reloads the task context even on this same route. */
export function CollectionReplayControl({
  job,
  part,
  canManage,
  offline = false,
}: {
  job: string;
  part: string;
  canManage: boolean;
  offline?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  const [inputs, setInputs] = useState<CollectionInputChoice[] | null>(null);
  const [input, setInput] = useState("");
  const [plan, setPlan] = useState<CollectionReplayView | null>(null);
  const [mode, setMode] = useState<"preview" | "lineage" | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function load(lineage = false) {
    setBusy(true);
    setError("");
    try {
      if (lineage) {
        const result = await readCollectionReplay(job);
        if (result.error) setError(result.error);
        else {
          setPlan(result.value ?? null);
          setMode("lineage");
          setStatus(null);
        }
      } else {
        const result = await listCollectionInputs(job, part);
        if (result.error) setError(result.error);
        else setInputs(result.value ?? []);
      }
    } catch {
      setError("unavailable");
    } finally {
      setBusy(false);
    }
  }
  async function preview() {
    if (!input) return;
    setBusy(true);
    setError("");
    setPlan(null);
    setStatus(null);
    setMode("preview");
    try {
      const result = await previewCollectionReplay(
        job,
        part,
        input,
        crypto.randomUUID(),
      );
      if (result.error) setError(result.error);
      else setPlan(result.value ?? null);
    } catch {
      setError("unavailable");
    } finally {
      setBusy(false);
    }
  }
  async function execute() {
    if (!plan || mode !== "preview") return;
    setBusy(true);
    setError("");
    try {
      const result = await submitCollectionReplay(
        plan.parent_job_id,
        plan.parent_part_id,
        plan.input_id,
        plan.request_id,
        plan.control_sha256,
      );
      if (result.error) setError(result.error);
      else setStatus(result.value?.state ?? null);
    } catch {
      setError("unavailable");
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    if (!plan) return;
    setBusy(true);
    setError("");
    try {
      const result = await readCollectionReplayState(plan.request_id);
      if (result.error) setError(result.error);
      else setStatus(result.value?.state ?? null);
    } catch {
      setError("unavailable");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div data-collection-replay={part}>
      <button
        type="button"
        className="button button-secondary"
        onClick={() => {
          dialog.current?.showModal();
          if (inputs === null && !busy) void load(offline);
        }}
      >
        {canManage ? "离线重处理" : "转换输入与血缘"}
      </button>
      <dialog ref={dialog} className={styles.dialog} aria-labelledby={title}>
        <div className={styles.header}>
          <h3 id={title}>行情与因子离线重处理</h3>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => dialog.current?.close()}
          >
            关闭重处理
          </button>
        </div>
        <p>
          只使用已保存的完整转换输入，不访问供应商。生成独立任务及新发布，旧版本和失败证据保留。关闭窗口不会取消已提交任务。
        </p>
        <p>
          保留原始获取时间和未知可知时间；本次发布时间不代表新行情或 PIT
          就绪。原始输入可能含整年来源片段，标准记录数量由裁剪及门禁后确定。
        </p>
        <div className={styles.actions}>
          <button
            type="button"
            className="button button-secondary"
            disabled={busy}
            onClick={() => void load()}
          >
            读取转换输入
          </button>
          <button
            type="button"
            className="button button-secondary"
            disabled={busy}
            onClick={() => void load(true)}
          >
            读取此任务血缘
          </button>
        </div>
        {busy && <p role="status">正在处理，请稍候…</p>}
        {error && <p role="alert">{errors[error] ?? errors.unavailable}</p>}
        {inputs?.length === 0 && (
          <p role="status">
            此分段未保存完整转换检查点。不能仅凭下载完成或 HTTP
            回执执行离线重处理。
          </p>
        )}
        {inputs && inputs.length > 0 && (
          <>
            <label>
              选择完整转换输入
              <select
                aria-label="选择完整转换输入"
                disabled={busy || Boolean(status)}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  setPlan(null);
                  setMode(null);
                  setStatus(null);
                }}
              >
                <option value="">请选择输入版本</option>
                {inputs.map((v) => (
                  <option key={v.input_id} value={v.input_id}>
                    {v.prepared_at} · {v.row_count} 行 · {v.mapping_version} ·{" "}
                    {v.input_id}
                  </option>
                ))}
              </select>
            </label>
            {canManage && (
              <button
                type="button"
                className="button button-secondary"
                disabled={!input || busy || Boolean(status)}
                onClick={() => void preview()}
              >
                预览离线重处理
              </button>
            )}
          </>
        )}
        {plan && (
          <div className={styles.summary} data-replay-request={plan.request_id}>
            <h4>
              {mode === "preview" ? "待确认的离线计划" : "已保存的离线意图"}
            </h4>
            <dl>
              <dt>证券与范围</dt>
              <dd>
                {plan.instrument_name} · {plan.instrument_id} ·{" "}
                {plan.field ?? "行情"}
                <br />
                {plan.start} 至 {plan.end}（结束不含，{plan.timezone}）
              </dd>
              <dt>来源与输入</dt>
              <dd>
                {plan.source} · {plan.row_count} 行原始输入 · 供应商调用 0 次
              </dd>
              <dt>转换与规则</dt>
              <dd>
                {plan.original_mapping} → {plan.target_mapping}
                <br />
                {plan.rule_version}
              </dd>
              <dt>核验与上限</dt>
              <dd>
                {plan.verified_bytes} 字节；最多 {plan.limits.max_rows} 行 /{" "}
                {plan.limits.max_bytes} 字节 / {plan.limits.max_receipts} 份回执
              </dd>
              <dt>新任务</dt>
              <dd>{plan.request_id}</dd>
              <dt>父任务 / 分段</dt>
              <dd>
                {plan.parent_job_id}
                <br />
                {plan.parent_part_id}
              </dd>
            </dl>
            <details>
              <summary>来源获取时间与输入摘要</summary>
              <p>
                {plan.input_id}
                <br />
                {plan.input_sha256}
              </p>
              {plan.original_acquired_at.length === 0 && (
                <p>来源实际获取时间未知；缓存重读回执不证明来源更新。</p>
              )}
              {plan.original_acquired_at.map((at) => (
                <p key={at}>
                  <time dateTime={at}>{at}</time>
                </p>
              ))}
            </details>
            {canManage && mode === "preview" && !status && (
              <button
                type="button"
                className="button button-primary"
                disabled={busy}
                onClick={() => void execute()}
              >
                确认创建离线重处理任务
              </button>
            )}
            {(status || mode === "lineage") && (
              <button
                type="button"
                className="button button-secondary"
                disabled={busy}
                onClick={() => void refresh()}
              >
                刷新离线处理结果
              </button>
            )}
            {status && (
              <p role="status" data-replay-state={status}>
                任务状态：{states[status] ?? "待核对"}。接受和排队不等于已发布。
              </p>
            )}
            {(status || mode === "lineage") && (
              <a
                className="button button-primary"
                href={`/workspace/data?data_view=collect&collector_job=${encodeURIComponent(plan.request_id)}`}
              >
                查看新任务与发布
              </a>
            )}
          </div>
        )}
      </dialog>
    </div>
  );
}
