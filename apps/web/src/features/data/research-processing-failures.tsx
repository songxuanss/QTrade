"use client";

import { useState } from "react";
import { readResearchProcessingFailures } from "../../app/workspace/research-collection-actions";

type Failure = {
  failure_id: string;
  checker_version: string;
  mapping_version: string;
  batch_id: string;
  normalized_record_count: null;
  failed_at: string;
};

export function ResearchProcessingFailures({ job }: { job: string }) {
  const [records, setRecords] = useState<Failure[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  async function load() {
    setBusy(true);
    setFailed(false);
    try {
      const result = await readResearchProcessingFailures(job);
      if (result.error) setFailed(true);
      else setRecords(result.value);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="collector-quality">
      <button
        className="button button-secondary"
        type="button"
        disabled={busy}
        onClick={() => void load()}
      >
        {busy ? "读取转换检查…" : "查看转换检查"}
      </button>
      {failed && <p role="status">转换检查暂不可读取，请重试。</p>}
      {records?.length === 0 && (
        <p role="status">未取得转换阻断记录，不能据此认定质量检查通过。</p>
      )}
      {records && records.length > 0 && (
        <div aria-label="研究转换阻断记录">
          <p>历史转换阻断不代表当前发布状态。</p>
          {records.map((record) => (
            <details key={record.failure_id} open={records.length === 1}>
              <summary>来源转换校验失败 · 标准记录数量未知</summary>
              <p>
                检查版本：{record.checker_version} · 转换版本：
                {record.mapping_version}
              </p>
              <p>已留存原始批次：{record.batch_id}</p>
              <p>
                记录时间：
                <time dateTime={record.failed_at}>
                  {new Date(record.failed_at).toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                  })}
                </time>
                （北京时间）
              </p>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}
