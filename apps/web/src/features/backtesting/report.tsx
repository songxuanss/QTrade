import Link from "next/link";
import type {
  BacktestManifestInfo,
  BacktestReportInfo,
  BacktestRunInfo,
} from "../auth/server";

function cash(value: string, currency: string) {
  const number = Number(value);
  return Number.isFinite(number)
    ? new Intl.NumberFormat("zh-CN", {
        style: "currency",
        currency,
        maximumFractionDigits: 2,
      }).format(number)
    : "—";
}

function percent(value: string | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function ratio(value: string | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return Number(value).toFixed(2);
}

function EquityChart({
  report,
  benchmarkId,
}: {
  report: BacktestReportInfo;
  benchmarkId: string | null;
}) {
  const points = report.equity_curve;
  const stride = Math.max(1, Math.ceil(points.length / 240));
  const sampled = points.filter((_, index) => index % stride === 0);
  if (sampled.at(-1) !== points.at(-1) && points.length > 1)
    sampled.push(points.at(-1)!);
  const values = sampled.map((point) => Number(point.equity));
  const benchmarkValues = sampled.map((point) =>
    point.benchmark_equity == null ? null : Number(point.benchmark_equity),
  );
  const combinedValues = [
    ...values,
    ...benchmarkValues.filter((value) => value != null),
  ];
  const min = Math.min(...combinedValues);
  const max = Math.max(...combinedValues);
  const spread = max - min || Math.abs(max) * 0.01 || 1;
  const path = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * 960 + 20;
      const y = 236 - ((value - min) / spread) * 204;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  let benchmarkPath = "";
  let hasPreviousBenchmark = false;
  benchmarkValues.forEach((value, index) => {
    if (value == null) {
      hasPreviousBenchmark = false;
      return;
    }
    const x = (index / Math.max(benchmarkValues.length - 1, 1)) * 960 + 20;
    const y = 236 - ((value - min) / spread) * 204;
    benchmarkPath += `${hasPreviousBenchmark ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)} `;
    hasPreviousBenchmark = true;
  });
  const area = `${path} L980,252 L20,252 Z`;
  return (
    <svg
      className="backtest-equity-chart"
      viewBox="0 0 1000 280"
      role="img"
      aria-label={`${benchmarkId ? "组合与基准对比" : "权益曲线"}，从 ${cash(String(values[0]), report.currency)} 到 ${cash(String(values.at(-1)), report.currency)}`}
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id="equity-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity=".24" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[42, 96, 150, 204, 258].map((y) => (
        <line
          key={y}
          x1="20"
          x2="980"
          y1={y}
          y2={y}
          className="backtest-chart-gridline"
        />
      ))}
      <path d={area} fill="url(#equity-fill)" />
      <path d={path} className="backtest-chart-line" />
      {benchmarkPath && (
        <path d={benchmarkPath} className="backtest-benchmark-line" />
      )}
      <text x="20" y="276" className="backtest-chart-label">
        {new Date(points[0].timestamp).toLocaleString("zh-CN")}
      </text>
      <text x="980" y="276" textAnchor="end" className="backtest-chart-label">
        {new Date(points.at(-1)!.timestamp).toLocaleString("zh-CN")}
      </text>
      <text x="20" y="24" className="backtest-chart-label">
        {cash(String(max), report.currency)}
      </text>
      <text x="20" y="252" className="backtest-chart-label">
        {cash(String(min), report.currency)}
      </text>
    </svg>
  );
}

function DrawdownChart({ report }: { report: BacktestReportInfo }) {
  const points = report.equity_curve;
  const stride = Math.max(1, Math.ceil(points.length / 240));
  const sampled = points.filter((_, index) => index % stride === 0);
  if (sampled.at(-1) !== points.at(-1) && points.length > 1)
    sampled.push(points.at(-1)!);
  const values = sampled.map((point) => Number(point.drawdown));
  const max = Math.max(...values, 0);
  const path = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * 960 + 20;
      const y = 38 + (max > 0 ? (value / max) * 194 : 0);
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <section className="panel backtest-report-chart-panel backtest-drawdown-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">RISK PROFILE</p>
          <h2>回撤曲线</h2>
        </div>
        <span className="backtest-report-totals">
          最大回撤{" "}
          {percent(
            report.metrics?.max_drawdown ?? report.summary.ending_drawdown,
          )}
        </span>
      </div>
      <svg
        className="backtest-equity-chart backtest-drawdown-chart"
        viewBox="0 0 1000 260"
        role="img"
        aria-label={`回撤曲线，最大回撤 ${percent(report.metrics?.max_drawdown ?? report.summary.ending_drawdown)}`}
        preserveAspectRatio="none"
      >
        {[42, 96, 150, 204, 252].map((y) => (
          <line
            key={y}
            x1="20"
            x2="980"
            y1={y}
            y2={y}
            className="backtest-chart-gridline"
          />
        ))}
        <path
          d={`${path} L980,38 L20,38 Z`}
          className="backtest-drawdown-area"
        />
        <path d={path} className="backtest-drawdown-line" />
        <text x="20" y="254" className="backtest-chart-label">
          {percent(String(max))}
        </text>
      </svg>
      <div className="backtest-report-legend">
        <span>
          <i aria-hidden="true" className="drawdown-key" />{" "}
          从历史权益峰值的回落幅度
        </span>
        <span>越接近 0 越好</span>
      </div>
    </section>
  );
}

export function BacktestReport({
  run,
  report,
  manifest,
  strategyLabel,
  datasetLabel,
}: {
  run: BacktestRunInfo;
  report: BacktestReportInfo | null;
  manifest: BacktestManifestInfo | null;
  strategyLabel?: string;
  datasetLabel?: string;
}) {
  if (!report)
    return (
      <div className="workspace-page">
        <Link href="/workspace/backtests" className="backtest-back-link">
          ← 返回回测实验
        </Link>
        <div className="panel backtest-report-unavailable">
          <p className="eyebrow">RUN REPORT</p>
          <h1>{run.status === "failed" ? "回测未完成" : "报告暂不可用"}</h1>
          <p>
            {run.error_code
              ? `任务错误代码：${run.error_code}`
              : run.status === "completed"
                ? "结果文件校验失败，请联系管理员检查该运行记录。"
                : `当前状态：${run.status} · ${run.progress}%`}
          </p>
        </div>
      </div>
    );

  const metrics = report.metrics;
  let parameters: Record<string, unknown> = {};
  let execution: Record<string, unknown> = {};
  try {
    const config = JSON.parse(manifest?.execution_config_json ?? "{}") as {
      execution_model?: Record<string, unknown>;
    };
    execution = config.execution_model ?? {};
  } catch {
    execution = {};
  }
  try {
    parameters = JSON.parse(manifest?.parameters_json ?? "{}") as Record<
      string,
      unknown
    >;
  } catch {
    parameters = {};
  }
  const cards = [
    {
      label: "累计收益",
      value: percent(report.summary.total_return),
      tone: Number(report.summary.total_return) >= 0 ? "positive" : "negative",
    },
    {
      label: "最大回撤",
      value: percent(metrics?.max_drawdown ?? report.summary.ending_drawdown),
      tone: "negative",
    },
    {
      label: "年化收益",
      value: percent(metrics?.annualized_return),
      tone: "neutral",
    },
    {
      label: "年化波动",
      value: percent(metrics?.annualized_volatility),
      tone: "neutral",
    },
    {
      label: "Sharpe",
      value: ratio(metrics?.sharpe_ratio),
      tone: "neutral",
    },
    {
      label: "基准收益",
      value: percent(metrics?.benchmark_return),
      tone: "neutral",
    },
  ];
  return (
    <div className="workspace-page backtest-report-page">
      <div className="backtest-report-nav">
        <Link href="/workspace/backtests" className="backtest-back-link">
          ← 回测实验
        </Link>
        <span>RUN REPORT</span>
      </div>
      <header className="page-heading backtest-report-heading">
        <div>
          <p className="eyebrow">
            IMMUTABLE RESULT · {report.determinism.engine}
          </p>
          <h1>回测报告</h1>
          <p className="page-description">
            Run {run.run_id.slice(0, 8)} ·{" "}
            {strategyLabel ?? `策略版本 ${run.strategy_version_id.slice(0, 8)}`}{" "}
            · {datasetLabel ?? `数据集 ${run.dataset_id.slice(0, 8)}`}
          </p>
        </div>
        <span className="backtest-status status-completed">已完成</span>
      </header>
      <section className="backtest-metric-grid" aria-label="绩效摘要">
        {cards.map((card) => (
          <article className="panel backtest-metric-card" key={card.label}>
            <span>{card.label}</span>
            <strong className={`metric-${card.tone}`}>{card.value}</strong>
          </article>
        ))}
      </section>
      <details className="panel backtest-metric-help">
        <summary>查看指标口径</summary>
        <p>
          最大回撤按权益峰值计算；年化收益按回测实际时间跨度计算。年化波动、Sharpe
          和 Sortino 只在输入中冻结了采样频率时计算，风险无风险利率按 0
          处理；无对应数据或样本不足时显示“—”。基准收益使用所选标的在回测区间内的首尾收盘价，按买入持有计算。
        </p>
      </details>
      <section
        className="backtest-secondary-metrics"
        aria-label="风险与交易统计"
      >
        {[
          ["Calmar", ratio(report.metrics?.calmar_ratio)],
          ["Sortino", ratio(report.metrics?.sortino_ratio)],
          ["胜率", percent(report.metrics?.win_rate)],
          ["盈亏比", ratio(report.metrics?.profit_factor)],
          ["换手率", `${ratio(report.metrics?.turnover)}×`],
          ["平均暴露", percent(report.metrics?.average_exposure)],
          ["最大暴露", percent(report.metrics?.max_exposure)],
          [
            "胜 / 负 / 已平仓",
            report.metrics
              ? `${report.metrics.winning_trades} / ${report.metrics.losing_trades} / ${report.metrics.trade_count}`
              : "—",
          ],
        ].map(([label, value]) => (
          <div className="backtest-secondary-metric" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </section>
      <section className="panel backtest-report-chart-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">PORTFOLIO VALUE</p>
            <h2>权益曲线</h2>
          </div>
          <div className="backtest-report-totals">
            <span>期末权益</span>
            <strong>
              {cash(report.summary.final_equity, report.currency)}
            </strong>
          </div>
        </div>
        <EquityChart report={report} benchmarkId={run.benchmark_id ?? null} />
        <div className="backtest-report-legend">
          <span>
            <i aria-hidden="true" /> QTrade 组合权益
          </span>
          {run.benchmark_id && (
            <span>
              <i aria-hidden="true" className="benchmark-key" />
              基准 {run.benchmark_id}
            </span>
          )}
          <span>
            {report.summary.sample_count.toLocaleString("zh-CN")} 个采样点
          </span>
        </div>
      </section>
      <DrawdownChart report={report} />
      <section className="panel backtest-report-table-panel backtest-positions-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">END OF RUN</p>
            <h2>期末持仓</h2>
          </div>
          <span className="backtest-count">
            {report.equity_curve.at(-1)?.positions.length ?? 0}
          </span>
        </div>
        {(report.equity_curve.at(-1)?.positions.length ?? 0) === 0 ? (
          <p className="backtest-empty">回测结束时没有未平仓持仓。</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table backtest-position-table">
              <thead>
                <tr>
                  <th>标的</th>
                  <th>持仓数量</th>
                  <th>市值</th>
                </tr>
              </thead>
              <tbody>
                {report.equity_curve.at(-1)!.positions.map((position) => (
                  <tr key={position.instrument_id}>
                    <td>{position.instrument_id}</td>
                    <td>{position.quantity}</td>
                    <td>{cash(position.market_value, report.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="panel backtest-report-table-panel backtest-orders-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">ORDER OUTCOMES</p>
            <h2>订单结果</h2>
          </div>
          <span className="backtest-count">{report.orders.length}</span>
        </div>
        {report.orders.length === 0 ? (
          <p className="backtest-empty">本次运行没有生成订单。</p>
        ) : (
          <div className="table-scroll">
            <table className="data-table backtest-order-table">
              <thead>
                <tr>
                  <th>提交时间</th>
                  <th>标的</th>
                  <th>方向</th>
                  <th>数量</th>
                  <th>成交数量</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {report.orders
                  .slice(-100)
                  .reverse()
                  .map((order, index) => (
                    <tr key={`${order.submitted_at}-${index}`}>
                      <td>
                        {new Date(order.submitted_at).toLocaleString("zh-CN")}
                      </td>
                      <td>{order.instrument_id}</td>
                      <td>{order.side === "buy" ? "买入" : "卖出"}</td>
                      <td>{order.quantity}</td>
                      <td>{order.filled_quantity ?? "0"}</td>
                      <td>
                        {order.status === "filled"
                          ? "已成交"
                          : order.status === "rejected"
                            ? "已拒绝"
                            : order.status === "partially_filled_end_of_data"
                              ? "部分成交，剩余未成交"
                              : "未成交"}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {report.orders.length > 100 && (
              <p className="backtest-table-note">
                仅显示最近 100 条，共 {report.orders.length} 条订单。
              </p>
            )}
          </div>
        )}
      </section>
      <div className="backtest-report-lower-grid">
        <section className="panel backtest-report-table-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">EXECUTIONS</p>
              <h2>成交明细</h2>
            </div>
            <span className="backtest-count">{report.fills.length}</span>
          </div>
          {report.fills.length === 0 ? (
            <p className="backtest-empty">本次运行没有产生成交。</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table backtest-fills-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>标的</th>
                    <th>方向</th>
                    <th>数量</th>
                    <th>成交价</th>
                    <th>费用</th>
                  </tr>
                </thead>
                <tbody>
                  {report.fills
                    .slice(-100)
                    .reverse()
                    .map((fill, index) => (
                      <tr key={`${fill.filled_at}-${index}`}>
                        <td>
                          {new Date(fill.filled_at).toLocaleString("zh-CN")}
                        </td>
                        <td>{fill.instrument_id}</td>
                        <td
                          className={
                            fill.side === "buy"
                              ? "metric-positive"
                              : "metric-negative"
                          }
                        >
                          {fill.side === "buy" ? "买入" : "卖出"}
                        </td>
                        <td>{fill.filled_quantity}</td>
                        <td>{cash(fill.price, report.currency)}</td>
                        <td>{cash(fill.fee, report.currency)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
              {report.fills.length > 100 && (
                <p className="backtest-table-note">
                  仅显示最近 100 条，共 {report.fills.length} 条成交。
                </p>
              )}
            </div>
          )}
        </section>
        <aside
          className="panel backtest-report-inputs"
          aria-label="回测运行与输入"
        >
          <p className="eyebrow">FROZEN INPUTS</p>
          <h2>运行与输入</h2>
          <dl>
            <div>
              <dt>时间范围</dt>
              <dd>
                {new Date(run.start).toLocaleString("zh-CN")} —{" "}
                {new Date(run.end).toLocaleString("zh-CN")}
              </dd>
            </div>
            <div>
              <dt>初始资金</dt>
              <dd>{cash(run.initial_cash, run.currency)}</dd>
            </div>
            <div>
              <dt>费用模型</dt>
              <dd>{run.cost_model_version}</dd>
            </div>
            <div>
              <dt>最小交易单位</dt>
              <dd>{String(execution.lot_size ?? "未记录")} 股</dd>
            </div>
            <div>
              <dt>手续费 / 滑点</dt>
              <dd>
                {String(execution.fee_bps ?? "—")} /{" "}
                {String(execution.slippage_bps ?? "—")} bps
              </dd>
            </div>
            <div>
              <dt>最低佣金 / 卖出税</dt>
              <dd>
                {String(execution.minimum_fee ?? "0")} 元 /{" "}
                {String(execution.sell_tax_bps ?? "0")} bps
              </dd>
            </div>
            <div>
              <dt>最小价位 / 交收</dt>
              <dd>
                {String(execution.tick_size ?? "0")} 元 /{" "}
                {execution.settlement_days === 1 ? "T+1" : "T+0"}
              </dd>
            </div>
            <div>
              <dt>成交量占比上限</dt>
              <dd>
                {execution.max_volume_fraction == null
                  ? "—"
                  : percent(String(execution.max_volume_fraction))}
              </dd>
            </div>
            <div>
              <dt>交易时段 / 时区</dt>
              <dd>
                {String(execution.session_open ?? "全天")}–
                {String(execution.session_close ?? "")} ·{" "}
                {String(execution.timezone_name ?? "UTC")}
              </dd>
            </div>
            <div>
              <dt>日内价格限制</dt>
              <dd>
                {execution.daily_price_limit_fraction == null
                  ? "无"
                  : percent(String(execution.daily_price_limit_fraction))}
              </dd>
            </div>
            <div>
              <dt>策略参数</dt>
              <dd className="backtest-hash">
                {Object.keys(parameters).length
                  ? JSON.stringify(parameters)
                  : "未设置"}
              </dd>
            </div>
            <div>
              <dt>执行模型 SHA-256</dt>
              <dd className="backtest-hash">
                {manifest?.execution_config_sha256 ?? "—"}
              </dd>
            </div>
            <div>
              <dt>代码 / 数据集 SHA-256</dt>
              <dd className="backtest-hash">
                {manifest?.code_sha256?.slice(0, 16) ?? "—"} /{" "}
                {manifest?.dataset_sha256?.slice(0, 16) ?? "—"}
              </dd>
            </div>
            <div>
              <dt>基准标的</dt>
              <dd>{run.benchmark_id ?? "未设置"}</dd>
            </div>
            <div>
              <dt>数据集</dt>
              <dd>{run.dataset_id}</dd>
            </div>
            <div>
              <dt>执行引擎 / seed</dt>
              <dd>
                {manifest?.engine_name
                  ? `${manifest.engine_name} ${manifest.engine_version}`
                  : report.determinism.engine}{" "}
                / {report.determinism.seed}
              </dd>
            </div>
          </dl>
        </aside>
      </div>
    </div>
  );
}
