import Link from "next/link";
import type { BacktestComparisonInfo } from "../auth/server";

function curvePath(values: string[]) {
  const stride = Math.max(1, Math.ceil(values.length / 240));
  const numbers = values.filter((_, index) => index % stride === 0).map(Number);
  if (values.length > 1 && values.length % stride !== 1)
    numbers.push(Number(values.at(-1)));
  const min = Math.min(...numbers);
  const max = Math.max(...numbers);
  const span = max - min || 1;
  return numbers
    .map((value, index) => {
      const x = numbers.length < 2 ? 50 : (index / (numbers.length - 1)) * 100;
      const y = 88 - ((value - min) / span) * 76;
      return `${index ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

const metrics: [
  string,
  (run: BacktestComparisonInfo["runs"][number]) => string,
][] = [
  [
    "总收益",
    (run) => `${(Number(run.result.summary.total_return) * 100).toFixed(2)}%`,
  ],
  ["年化收益", (run) => displayPercent(run.result.metrics?.annualized_return)],
  ["最大回撤", (run) => displayPercent(run.result.metrics?.max_drawdown)],
  ["Sharpe", (run) => displayNumber(run.result.metrics?.sharpe_ratio)],
  ["Sortino", (run) => displayNumber(run.result.metrics?.sortino_ratio)],
  ["Calmar", (run) => displayNumber(run.result.metrics?.calmar_ratio)],
  ["胜率", (run) => displayPercent(run.result.metrics?.win_rate)],
  ["盈亏比", (run) => displayNumber(run.result.metrics?.profit_factor)],
  ["换手率", (run) => `${displayNumber(run.result.metrics?.turnover)}×`],
];

function displayPercent(value: string | null | undefined) {
  return value == null ? "不可计算" : `${(Number(value) * 100).toFixed(2)}%`;
}

function displayNumber(value: string | null | undefined) {
  return value == null ? "不可计算" : Number(value).toFixed(2);
}

export function BacktestComparison({
  comparison,
}: {
  comparison: BacktestComparisonInfo;
}) {
  return (
    <div className="workspace-page backtest-report-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">RESEARCH / EXPERIMENTS</p>
          <h1>回测对比</h1>
          <p className="page-description">
            对照冻结输入、权益路径与绩效指标，识别策略结果的差异来源。
          </p>
        </div>
        <Link className="button button-secondary" href="/workspace/backtests">
          返回回测实验
        </Link>
      </div>
      <section className="panel backtest-report-panel" aria-label="可比性说明">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">COMPARABILITY</p>
            <h2>
              {comparison.comparable ? "输入范围可直接比较" : "存在不可比输入"}
            </h2>
          </div>
        </div>
        {comparison.warnings.length ? (
          <ul className="backtest-comparison-warnings">
            {comparison.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : (
          <p>数据集、区间、币种和基准一致；策略或执行参数差异已列入下表。</p>
        )}
        {comparison.input_differences.length > 0 && (
          <p>输入差异：{comparison.input_differences.join("、")}</p>
        )}
      </section>
      <section
        className="panel backtest-report-panel"
        aria-label="权益曲线对比"
      >
        <div className="panel-heading">
          <div>
            <p className="eyebrow">NORMALIZED EQUITY</p>
            <h2>权益路径（起点归一至 100）</h2>
          </div>
        </div>
        <div className="backtest-comparison-curves">
          {comparison.runs.map((run, index) => {
            const values = run.result.equity_curve.map((point) => point.equity);
            const initial = Number(values[0] ?? 1);
            const normalized = values.map((value) =>
              String((Number(value) / initial) * 100),
            );
            return (
              <figure key={run.run_id}>
                <figcaption>Run {run.run_id.slice(0, 8)}</figcaption>
                <svg
                  viewBox="0 0 100 100"
                  role="img"
                  aria-label={`Run ${run.run_id.slice(0, 8)} 归一化权益曲线`}
                >
                  <path d="M0 88H100" className="backtest-chart-axis" />
                  <path
                    d={curvePath(normalized)}
                    className={
                      index
                        ? "backtest-comparison-line line-secondary"
                        : "backtest-comparison-line"
                    }
                  />
                </svg>
              </figure>
            );
          })}
        </div>
      </section>
      <section className="panel backtest-report-panel" aria-label="指标对比">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">METRIC DIFFERENCES</p>
            <h2>核心指标</h2>
          </div>
        </div>
        <div className="backtest-table-scroll">
          <table className="backtest-comparison-table">
            <thead>
              <tr>
                <th scope="col">指标</th>
                {comparison.runs.map((run) => (
                  <th scope="col" key={run.run_id}>
                    Run {run.run_id.slice(0, 8)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metrics.map(([label, value]) => (
                <tr key={label}>
                  <th scope="row">{label}</th>
                  {comparison.runs.map((run) => (
                    <td key={run.run_id}>{value(run)}</td>
                  ))}
                </tr>
              ))}
              <tr>
                <th scope="row">数据集</th>
                {comparison.runs.map((run) => (
                  <td key={`${run.run_id}-dataset`}>
                    {run.dataset_id.slice(0, 8)}
                  </td>
                ))}
              </tr>
              <tr>
                <th scope="row">策略版本</th>
                {comparison.runs.map((run) => (
                  <td key={`${run.run_id}-version`}>
                    {run.strategy_version_id.slice(0, 8)}
                  </td>
                ))}
              </tr>
              <tr>
                <th scope="row">区间</th>
                {comparison.runs.map((run) => (
                  <td key={`${run.run_id}-range`}>
                    {new Date(run.start).toLocaleDateString("zh-CN")} —{" "}
                    {new Date(run.end).toLocaleDateString("zh-CN")}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
