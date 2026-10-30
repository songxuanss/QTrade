import Link from "next/link";
export type CoverageReport = {
  dataset_id: string;
  bar_count: number;
  checks: string[];
  coverage?: {
    research_readiness: string;
    coverage_status: string;
    expected_trading_days?: number;
    observed_trading_days?: number;
    missing_trading_days?: string[];
    observed_start?: string;
    observed_end?: string;
  };
};
export function DatasetQualityReport({
  report,
  unavailable,
}: {
  report: CoverageReport | null;
  unavailable: boolean;
}) {
  return (
    <section className="panel data-section" id="dataset-quality">
      <div className="data-section-heading">
        <div>
          <p className="eyebrow">COVERAGE & READINESS</p>
          <h2>数据完整性报告</h2>
        </div>
        <Link className="button button-secondary" href="/workspace/data">
          关闭报告
        </Link>
      </div>
      {unavailable || !report ? (
        <p className="data-notice" role="alert">
          质量报告暂不可用，请刷新重试。
        </p>
      ) : (
        <>
          <p className="page-description">
            本报告区分来源采集完成、已观察到的交易日和量化研究准备程度。文件校验通过不代表市场数据没有遗漏。
          </p>
          <div className="data-metrics">
            <article className="panel data-metric">
              <span>标准行情记录</span>
              <strong>{report.bar_count.toLocaleString()}</strong>
              <small>已通过结构与来源校验</small>
            </article>
            <article className="panel data-metric">
              <span>交易日覆盖</span>
              <strong>
                {report.coverage?.observed_trading_days ?? "—"} /{" "}
                {report.coverage?.expected_trading_days ?? "—"}
              </strong>
              <small>观察到 / 来源日历预期</small>
            </article>
            <article className="panel data-metric">
              <span>研究准备程度</span>
              <strong className="readiness-label">
                {report.coverage?.research_readiness === "incomplete"
                  ? "有缺口"
                  : report.coverage?.research_readiness === "limited"
                    ? "基础行情"
                    : "待核验"}
              </strong>
              <small>不代表回测或交易已就绪</small>
            </article>
          </div>
          {(report.coverage?.missing_trading_days?.length ?? 0) > 0 && (
            <p className="data-notice" role="alert">
              缺少交易日：{report.coverage?.missing_trading_days?.join("、")}
              。缺失原因尚未确认，不能当作停牌或休市。
            </p>
          )}
          {report.coverage?.observed_start && (
            <p className="page-description">
              实际首末记录（UTC）：{report.coverage.observed_start} —{" "}
              {report.coverage.observed_end}
            </p>
          )}
          <p className="data-description">
            仍需核验：历史股票池、复权与公司行为、历史交易状态、财务公告可见时间，以及盘中记录的完整性。当前数据适合查看未复权行情；不作为完整量化数据包或实盘就绪证明。
          </p>
        </>
      )}
    </section>
  );
}
