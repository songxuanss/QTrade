"use client";

import { useState } from "react";
import {
  readCollectorQuality,
  readCollectorProcessingFailures,
} from "../../app/workspace/collector-actions";
import type {
  CollectorQuality,
  CollectorProcessingFailure,
} from "./collector-types";

const statuses = {
  passed: "检查通过",
  warning: "带警告",
  blocked: "发布阻断",
  no_observations: "无观测",
};
const reasons: Record<string, string> = {
  raw_missing: "缺少转换前来源证据",
  record_invalid: "记录格式或数值不合法",
  type_mismatch: "数据类别不匹配",
  identity_mismatch: "标的或因子身份不匹配",
  period_mismatch: "周期不匹配",
  range_mismatch: "记录超出请求范围",
  duplicate: "存在重复记录",
  unit_mismatch: "单位不符合规则",
  time_label_unknown: "时间标签未经确认",
  source_suspect: "来源记录带可疑标记，需进一步核验",
  calendar_unknown: "缺少来源交易日历证据",
  calendar_invalid: "交易日历与记录不一致",
  missing_trade_dates: "缺少预期交易日，未确认停牌原因",
  intraday_completeness_unknown: "每日有观测不代表全部分钟完整",
  factor_definition_unknown: "因子定义或计算版本未知",
  factor_unit_unknown: "因子单位未知",
  factor_null: "来源提供合法空值",
  knowledge_unknown: "历史可知时间未确认，不支持据此认定 PIT",
  future_knowledge: "可知时间晚于检查时点",
};

export function CollectionQualityPanel({
  job,
  part,
}: {
  job: string;
  part: string;
}) {
  const [reports, setReports] = useState<CollectorQuality[] | null>(null);
  const [processingFailures, setProcessingFailures] = useState<
    CollectorProcessingFailure[] | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  async function load() {
    setBusy(true);
    setFailed(false);
    try {
      const [result, rejected] = await Promise.all([
        readCollectorQuality(job, part),
        readCollectorProcessingFailures(job, part),
      ]);
      if (result.reports) setReports(result.reports);
      else setFailed(true);
      if (rejected.failures) setProcessingFailures(rejected.failures);
      else setFailed(true);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }
  // Load on demand so a large pool does not fan out one HTTP request per part.
  return (
    <div className="collector-quality">
      <button
        type="button"
        className="button button-secondary"
        disabled={busy}
        onClick={() => void load()}
      >
        {busy ? "读取质量报告…" : "查看质量报告"}
      </button>
      {failed && (
        <p role="status">质量报告暂不可读取，请重试或检查采集服务。</p>
      )}
      {reports?.length === 0 && processingFailures?.length === 0 && (
        <p role="status">
          未取得质量报告，不能视为检查通过。旧产物或转换失败任务可能尚未经过本规则。
        </p>
      )}
      {processingFailures && processingFailures.length > 0 && (
        <div aria-label="历史转换阻断记录">
          <p>转换阶段曾被阻断；这些历史记录不代表当前发布状态。</p>
          {processingFailures.map((failure) => (
            <details
              key={failure.failure_id}
              open={processingFailures.length === 1}
            >
              <summary>来源格式或转换校验失败 · 未生成可发布记录</summary>
              <p>
                检查版本：{failure.checker_version} · 转换版本：
                {failure.mapping_version}
              </p>
              <p>
                尝试 {failure.attempt} · 原始回执{" "}
                {failure.raw_capture_ids.length} 份 · 标准记录数量未知
              </p>
              <p>
                记录时间：
                <time dateTime={failure.failed_at}>
                  {new Date(failure.failed_at).toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                  })}
                </time>
                （北京时间）
              </p>
            </details>
          ))}
        </div>
      )}
      {reports && reports.length > 0 && (
        <div aria-label="采集质量报告">
          {reports.map((report) => (
            <details key={report.quality_id} open={reports.length === 1}>
              <summary>
                {statuses[report.status]} · {report.rule_version} ·{" "}
                {report.record_count} 条记录
              </summary>
              <p className="data-footnote">
                检查时间：{report.checked_at}
                。结论仅适用于该规则与输入，不代表研究或回测就绪。
              </p>
              <p className="data-footnote">
                报告版本：<code>{report.quality_id}</code>
              </p>
              {report.issues.length > 0 && (
                <ul>
                  {report.issues.map((issue) => (
                    <li key={issue.code}>
                      {issue.severity === "block" ? "阻断" : "警告"}：
                      {reasons[issue.code] ?? issue.code}
                    </li>
                  ))}
                </ul>
              )}
            </details>
          ))}
        </div>
      )}
    </div>
  );
}
