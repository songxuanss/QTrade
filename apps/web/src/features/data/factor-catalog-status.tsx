"use client";

import { useState } from "react";
import { readCollectorFactorCatalog } from "../../app/workspace/collector-actions";
import styles from "./factor-catalog-status.module.css";
import type { FactorCatalogSnapshot } from "./collector-types";

const labels = {
  fresh: "目录已更新",
  stale: "正在使用缓存目录",
  loading: "首次目录更新中",
  unavailable: "目录暂不可用",
  unconfigured: "因子来源尚未配置",
};
const failures: Record<string, string> = {
  timeout: "更新超时",
  unauthorized: "来源授权未通过",
  rate_limited: "来源限流",
  unverified: "缓存未通过校验",
  unavailable: "来源连接或存储暂不可用",
};

export function FactorCatalogStatus({
  snapshot,
  onUpdated,
}: {
  snapshot: FactorCatalogSnapshot | null;
  onUpdated: (snapshot: FactorCatalogSnapshot) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  async function refresh() {
    setBusy(true);
    setError("");
    setConfirmed(false);
    try {
      const result = await readCollectorFactorCatalog();
      if (result.snapshot) {
        onUpdated(result.snapshot);
        setConfirmed(true);
      } else setError(result.error ?? "unavailable");
    } catch {
      setError("unavailable");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div
      className={styles.status}
      aria-label="因子目录状态"
      data-factor-catalog-status={snapshot?.status ?? "unavailable"}
      data-factor-catalog-sha256={snapshot?.content_sha256 ?? ""}
    >
      <p role="status" className={styles.title}>
        {snapshot ? labels[snapshot.status] : "目录状态读取失败"}
        {snapshot?.refreshing ? " · 后台更新中" : ""}
        {snapshot?.error_code
          ? ` · ${failures[snapshot.error_code] ?? "更新失败"}`
          : ""}
      </p>
      <p>
        {snapshot?.fetched_at
          ? `最近取得目录：${new Date(snapshot.fetched_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}（北京时间）`
          : "最近取得时间未知"}
        。目录仅用于字段选择，历史权限与范围以采集响应为准。
      </p>
      <p>
        {snapshot?.status === "stale"
          ? "缓存可能已过期；后台会重新获取目录。"
          : "后台定期更新，已有任务和数据可继续查询。"}
      </p>
      <button
        type="button"
        className={styles.refresh}
        disabled={busy}
        onClick={() => void refresh()}
      >
        {busy ? "正在读取目录状态…" : "更新目录状态"}
      </button>
      {error && (
        <p role="alert">
          目录状态读取失败，请稍后重试。
          {failures[error] ?? "当前连接或权限不可用"}
        </p>
      )}
      {confirmed && <p role="status">目录状态已更新，输入与已选因子保留。</p>}
      {snapshot?.content_sha256 && (
        <details>
          <summary>目录版本</summary>
          <code>{snapshot.content_sha256}</code>
        </details>
      )}
    </div>
  );
}
