"use client";
import { useEffect, useId, useRef, useState } from "react";
import styles from "./research-response-receipts.module.css";
import { readResearchResponseReceipts } from "../../app/workspace/research-collection-actions";

type Receipt = {
  receipt_id: string;
  source: string;
  instrument_id: string;
  http_status: number | null;
  body_bytes: number;
  body_sha256: string;
  complete: boolean;
  captured_at: string;
  mapping_version: string;
};
type Page = { request_id: string; receipts: Receipt[]; next_id: string | null };

/** Transport facts are historical evidence, never a successful-download badge.
 * Fetch only on demand; immutable response bodies have no browser endpoint. */
export function ResearchResponseReceipts({ job }: { job: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [page, setPage] = useState<Page | null>(null);
  const [busy, setBusy] = useState(false),
    [failed, setFailed] = useState(false);
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const generation = useRef(0);
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  async function load(next: (string | undefined)[]) {
    const request = ++generation.current;
    setBusy(true);
    try {
      const value = await readResearchResponseReceipts(job, next.at(-1));
      if (request !== generation.current) return;
      if (value.error) setFailed(true);
      else {
        setPage(value.value);
        setCursors(next);
        setFailed(false);
      }
    } catch {
      if (request === generation.current) setFailed(true);
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  return (
    <div data-response-receipts={job}>
      <button
        type="button"
        className="button button-secondary"
        onClick={() => {
          dialog.current?.showModal();
          if (!page && !busy) void load([undefined]);
        }}
      >
        查看来源响应回执
      </button>
      <dialog ref={dialog} aria-labelledby={titleId} className={styles.dialog}>
        <div className={styles.header}>
          <h3 id={titleId}>来源响应回执</h3>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => dialog.current?.close()}
          >
            关闭响应回执
          </button>
        </div>
        <p>
          仅展示已记录的响应元数据。HTTP
          200或正文完整不代表数据通过校验、已发布或可用于研究。
        </p>
        <button
          type="button"
          className="button button-secondary"
          disabled={busy}
          onClick={() => void load([undefined])}
        >
          刷新响应回执
        </button>
        {busy && <p>正在读取回执…</p>}
        {failed && (
          <p role="alert">响应回执读取失败，旧记录仅供参考；请重试。</p>
        )}
        {page && (
          <>
            <p role="status">
              响应回执第 {cursors.length} 页 · 本页 {page.receipts.length} 条
            </p>
            {!page.receipts.length && (
              <p>
                暂无已记录回执；可能尚未收到响应或旧版本未记录，不能据此判断数据质量。
              </p>
            )}
            {page.receipts.map((r) => (
              <details
                key={r.receipt_id}
                data-response-id={r.receipt_id}
                className={styles.record}
              >
                <summary>
                  {r.instrument_id} ·{" "}
                  {r.http_status === null
                    ? "未取得HTTP状态"
                    : `HTTP ${r.http_status}`}{" "}
                  · {r.body_bytes} 字节
                </summary>
                <p>
                  记录时间：
                  {new Date(r.captured_at).toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                  })}
                  （北京时间）
                </p>
                <p>
                  {r.complete
                    ? "接收时HTTP响应正文完整"
                    : "接收时响应正文不完整"}{" "}
                  · 来源：{r.source} · 转换版本：{r.mapping_version}
                </p>
                <p>记录的正文SHA-256：{r.body_sha256}</p>
                <p>回执标识：{r.receipt_id}</p>
              </details>
            ))}
            <button
              type="button"
              className="button button-secondary"
              disabled={busy || cursors.length === 1}
              onClick={() => void load(cursors.slice(0, -1))}
            >
              上一页响应回执
            </button>
            <button
              type="button"
              className="button button-secondary"
              disabled={busy || !page.next_id}
              onClick={() =>
                page.next_id && void load([...cursors, page.next_id])
              }
            >
              下一页响应回执
            </button>
          </>
        )}
      </dialog>
    </div>
  );
}
