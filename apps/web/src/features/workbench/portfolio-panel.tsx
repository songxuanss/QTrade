import Link from "next/link";
import { EmptyState } from "../../components/ui/states";
import type { WorkspacePortfolioSummary } from "../auth/server";

function money(value: string, currency: string) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function percent(value: string) {
  return `${(Number(value) * 100).toFixed(2)}%`;
}

export function PortfolioPanel({
  summary,
  error,
}: {
  summary: WorkspacePortfolioSummary | null;
  error: boolean;
}) {
  return (
    <section
      className="panel portfolio-panel"
      aria-labelledby="portfolio-heading"
    >
      <div className="panel-heading">
        <div>
          <h2 id="portfolio-heading">
            组合与风险摘要
            <span className="heading-subtitle">PORTFOLIO & RISK</span>
          </h2>
          <p>权益、持仓和风险暴露汇总</p>
        </div>
        {summary && (
          <span
            className={`portfolio-source ${summary.source === "mock" ? "sample" : ""}`}
          >
            {summary.source === "mock"
              ? "演示组合 · 非真实账户"
              : summary.account_id}
          </span>
        )}
      </div>
      {error ? (
        <p className="portfolio-error" role="alert">
          暂时无法读取组合数据。账户连接状态不会影响其他工作区功能。
        </p>
      ) : !summary ? (
        <div className="portfolio-empty">
          <EmptyState
            headingLevel={3}
            title="暂无组合数据"
            description="连接 Paper 账户后，权益、持仓和未完成订单会显示在这里。"
            icon="trade"
            action={
              <Link
                href="/workspace/trading"
                className="button button-secondary"
              >
                查看交易执行
              </Link>
            }
          />
        </div>
      ) : (
        <>
          <div className="portfolio-metrics">
            <article>
              <span>账户权益</span>
              <strong>{money(summary.equity, summary.currency)}</strong>
              <small>{summary.account_id}</small>
            </article>
            <article>
              <span>现金</span>
              <strong>{money(summary.cash, summary.currency)}</strong>
              <small>可用余额快照</small>
            </article>
            <article>
              <span>持仓盈亏</span>
              <strong
                className={
                  Number(summary.unrealized_pnl) >= 0 ? "positive" : "negative"
                }
              >
                {money(summary.unrealized_pnl, summary.currency)}
              </strong>
              <small>未实现盈亏</small>
            </article>
            <article>
              <span>总敞口</span>
              <strong>{money(summary.gross_exposure, summary.currency)}</strong>
              <small>多空绝对市值</small>
            </article>
          </div>
          <div className="portfolio-risk-metrics">
            <div>
              <span>最大持仓集中度</span>
              <strong>{percent(summary.concentration)}</strong>
            </div>
            <div>
              <span>当前回撤</span>
              <strong>{percent(summary.drawdown)}</strong>
            </div>
            <div>
              <span>未完成订单</span>
              <strong>{summary.open_orders.toLocaleString("zh-CN")}</strong>
            </div>
            <div>
              <span>快照时间</span>
              <strong>
                {new Date(summary.as_of).toLocaleString("zh-CN", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </strong>
            </div>
          </div>
          <div className="portfolio-positions">
            <div className="portfolio-subheading">
              <h3>持仓</h3>
              <span>{summary.positions.length} 个标的</span>
            </div>
            {summary.positions.length ? (
              <div
                className="portfolio-table-scroll"
                role="region"
                aria-label="组合持仓"
                tabIndex={0}
              >
                <table>
                  <thead>
                    <tr>
                      <th>标的</th>
                      <th>数量</th>
                      <th>最新价</th>
                      <th>市值</th>
                      <th>未实现盈亏</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.positions.map((position) => (
                      <tr key={position.instrument_id}>
                        <th scope="row">{position.instrument_id}</th>
                        <td>
                          {Number(position.quantity).toLocaleString("zh-CN")}
                        </td>
                        <td>{money(position.mark_price, summary.currency)}</td>
                        <td>
                          {money(position.market_value, summary.currency)}
                        </td>
                        <td
                          className={
                            Number(position.unrealized_pnl) >= 0
                              ? "positive"
                              : "negative"
                          }
                        >
                          {money(position.unrealized_pnl, summary.currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="portfolio-no-positions">当前没有持仓。</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}
