"use client";

import { useState } from "react";
import {
  exportBacktestRun,
  exportWalkForward,
} from "../../app/workspace/backtest-actions";

function saveFile(filename: string, content: string, mediaType: string) {
  const blob = new Blob([content], { type: mediaType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function ExportButtons({
  runId,
  walkForwardId,
}: {
  runId?: string;
  walkForwardId?: string;
}) {
  const [busy, setBusy] = useState<"csv" | "html" | null>(null);
  const [error, setError] = useState("");

  async function download(format: "csv" | "html") {
    setError("");
    setBusy(format);
    const result = runId
      ? await exportBacktestRun(runId, format)
      : walkForwardId
        ? await exportWalkForward(walkForwardId, format)
        : { ok: false, error: "缺少导出对象" };
    setBusy(null);
    if (!result.ok || !result.filename || result.content == null) {
      setError(result.error ?? "导出失败");
      return;
    }
    saveFile(result.filename, result.content, result.mediaType ?? "text/plain");
  }

  return (
    <div className="backtest-export">
      <button
        type="button"
        className="button button-secondary"
        onClick={() => download("csv")}
        disabled={busy !== null}
      >
        {busy === "csv" ? "正在导出…" : "下载 CSV"}
      </button>
      <button
        type="button"
        className="button button-secondary"
        onClick={() => download("html")}
        disabled={busy !== null}
      >
        {busy === "html" ? "正在导出…" : "下载 HTML / 打印 PDF"}
      </button>
      {error && (
        <span className="backtest-export-error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
