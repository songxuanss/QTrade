import { createHistoryTaskAction } from "../../app/workspace/data-actions";

export type SourceOption = {
  source: string;
  authorization_id: string;
  granularities: string[];
  earliest: string | null;
};

export function HistoryDownloadForm({
  options,
  available,
}: {
  options: SourceOption[];
  available: boolean;
}) {
  const source = options.find((item) => item.source === "mairui.a-share.bar");
  const periods = [
    ["1d", "日线"],
    ["5m", "5 分钟"],
    ["15m", "15 分钟"],
    ["1h", "60 分钟"],
    ["1m", "1 分钟（Pro）"],
  ];
  return (
    <section
      className="panel data-section"
      id="history-download"
      aria-labelledby="history-download-title"
    >
      <div className="data-section-heading">
        <div>
          <p className="eyebrow">HISTORICAL BARS</p>
          <h2 id="history-download-title">日线与分钟历史下载</h2>
        </div>
        <span>未复权行情</span>
      </div>
      <p className="page-description">
        选择北京时间的起止日期，包含结束日。原始行情与交易日历一同保存，完成后自动生成对应周期的历史数据集。实际历史覆盖以来源返回与质量报告为准。
      </p>
      {!source && <p className="data-empty">历史行情来源尚未配置。</p>}
      <form action={createHistoryTaskAction} className="history-request-form">
        <label>
          A 股代码
          <input
            name="instrument_id"
            aria-label="历史行情股票代码"
            placeholder="例如 600519.SH"
            pattern="(?:CN\.)?\d{6}(?:\.(?:SH|SZ|BJ))?"
            required
          />
        </label>
        <label>
          数据周期
          <select
            name="granularity"
            aria-label="历史行情周期"
            defaultValue="1d"
            required
          >
            {periods.map(([value, label]) => (
              <option
                key={value}
                value={value}
                disabled={!source?.granularities.includes(value)}
              >
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          开始日期（北京时间）
          <input
            name="start_date"
            aria-label="历史行情开始日期"
            type="date"
            required
          />
        </label>
        <label>
          结束日期（北京时间，含当日）
          <input
            name="end_date"
            aria-label="历史行情结束日期"
            type="date"
            required
          />
        </label>
        <button
          className="button button-primary"
          disabled={!available || !source}
          type="submit"
        >
          创建历史行情任务
        </button>
      </form>
      {source && !source.granularities.includes("1m") && (
        <p className="page-description">
          1 分钟 Pro 未启用。日线、5／15／60 分钟可下载；Pro
          权限不足不会自动改用其他周期。
        </p>
      )}
    </section>
  );
}
