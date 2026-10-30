import Link from "next/link";
import { Icon } from "../../components/ui/icon";
import { EmptyState } from "../../components/ui/states";
import { PortfolioPanel } from "./portfolio-panel";
import type { WorkspacePortfolioSummary } from "../auth/server";

export function Overview({
  loginEnabled = false,
  authenticated = false,
  portfolio = null,
  portfolioError = false,
}: {
  loginEnabled?: boolean;
  authenticated?: boolean;
  portfolio?: WorkspacePortfolioSummary | null;
  portfolioError?: boolean;
}) {
  return (
    <div className="workspace-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">WORKSPACE OVERVIEW</p>
          <h1>工作台</h1>
          <p className="page-description">
            洞察市场，验证策略，让每一次交易有据可依。
          </p>
        </div>
        <span className="badge badge-neutral">
          <Icon name="lock" size={12} />
          {authenticated ? "身份已连接" : "等待身份连接"}
        </span>
      </div>
      <section className="welcome-banner" aria-labelledby="welcome-title">
        <div className="welcome-mark">
          <Icon name="activity" size={30} />
        </div>
        <div className="welcome-copy">
          <p className="section-kicker">从研究到执行</p>
          <h2 id="welcome-title">
            {authenticated ? "继续你的研究" : "建立你的交易工作区"}
          </h2>
          <p>
            {authenticated
              ? "查看数据采集、管理策略版本，追踪研究与风险操作的历史。"
              : "连接身份、选择数据、验证策略。研究记录与每个交易决策始终可追溯。"}
          </p>
        </div>
        <div className="welcome-action">
          {authenticated ? (
            <Link className="button button-primary" href="/workspace/data">
              进入工作区
              <Icon name="arrow" size={14} />
            </Link>
          ) : loginEnabled ? (
            <a className="button button-primary" href="/auth/login">
              登录工作区
              <Icon name="arrow" size={14} />
            </a>
          ) : (
            <button
              className="button button-primary"
              disabled
              title="身份服务连接后可登录"
            >
              登录工作区
              <Icon name="arrow" size={14} />
            </button>
          )}
          <span>统一身份认证 · 受控访问</span>
        </div>
      </section>
      {authenticated && (
        <PortfolioPanel summary={portfolio} error={portfolioError} />
      )}
      <div className="workspace-grid">
        <section className="panel market-panel" aria-labelledby="market-title">
          <div className="panel-heading">
            <div>
              <h2 id="market-title">
                市场观察<span className="heading-subtitle">MARKET WATCH</span>
              </h2>
              <p>关注你的市场与交易标的</p>
            </div>
            <Link href="/workspace/market" className="text-link">
              市场终端
              <Icon name="arrow" size={13} />
            </Link>
          </div>
          <div className="watch-table-heading">
            <span>标的 / 交易所</span>
            <span>最新价格</span>
            <span>涨跌幅</span>
            <span>数据状态</span>
          </div>
          <div className="market-empty-grid">
            <EmptyState
              title="让行情成为研究的起点"
              description={
                authenticated
                  ? "在数据管理中生成研究数据，再到市场终端添加自选标的并查看历史行情。"
                  : "登录工作区后，添加自选标的并连接授权数据源。"
              }
              icon="market"
              action={
                <Link
                  href="/workspace/data"
                  className="button button-secondary"
                >
                  查看数据管理
                  <Icon name="chevron" size={13} />
                </Link>
              }
            />
          </div>
          <div className="panel-footnote">
            <Icon name="clock" size={13} />
            {authenticated ? (
              "历史行情与实时连接状态请在市场终端查看"
            ) : (
              <>
                尚未连接行情数据<span>价格与质量状态将在此展示</span>
              </>
            )}
          </div>
        </section>
        <section className="panel risk-panel" aria-labelledby="risk-title">
          <div className="panel-heading">
            <div>
              <h2 id="risk-title">
                风险控制<span className="heading-subtitle">RISK CONTROL</span>
              </h2>
              <p>执行之前，先确定风险</p>
            </div>
            <span className="risk-shield">
              <Icon name="shield" size={20} />
            </span>
          </div>
          <div className="risk-summary">
            <span className="badge badge-safe">
              <Icon name="shield" size={12} />
              实盘交易默认关闭
            </span>
            <h3>安全从第一步开始</h3>
            <p>实盘启动需要账户授权、策略审批、风控配置与二次确认。</p>
          </div>
          <div className="risk-checklist">
            {["预交易风控", "订单幂等与对账", "账户与全局 Kill Switch"].map(
              (label) => (
                <div key={label}>
                  <span>
                    <Icon name="check" size={13} />
                  </span>
                  {label}
                  <span className="checklist-status">启动前必检</span>
                </div>
              ),
            )}
          </div>
          <Link href="/workspace/risk" className="panel-link">
            进入风控中心
            <Icon name="arrow" size={14} />
          </Link>
        </section>
        <section
          className="panel research-panel"
          aria-labelledby="research-title"
        >
          <div className="panel-heading">
            <div>
              <h2 id="research-title">
                研究工作流
                <span className="heading-subtitle">RESEARCH WORKFLOW</span>
              </h2>
              <p>从可验证的输入，到可复现的结果</p>
            </div>
          </div>
          <div className="workflow-steps">
            {[
              {
                number: "01",
                icon: "data" as const,
                title: "准备数据",
                text: "授权、质量与版本",
                href: "/workspace/data",
              },
              {
                number: "02",
                icon: "strategy" as const,
                title: "构建策略",
                text: "代码、参数与校验",
                href: "/workspace/strategies",
              },
              {
                number: "03",
                icon: "backtest" as const,
                title: "验证回测",
                text: "成本、表现与复现",
                href: "/workspace/backtests",
              },
            ].map((step) => (
              <Link
                key={step.number}
                href={step.href}
                className="workflow-step"
              >
                <span className="step-number mono">{step.number}</span>
                <Icon name={step.icon} size={22} />
                <h3>{step.title}</h3>
                <p>{step.text}</p>
                <Icon name="arrow" size={13} />
              </Link>
            ))}
          </div>
        </section>
        <section
          className="panel activity-panel"
          aria-labelledby="activity-title"
        >
          <div className="panel-heading">
            <div>
              <h2 id="activity-title">
                近期活动
                <span className="heading-subtitle">RECENT ACTIVITY</span>
              </h2>
              <p>工作区中的研究与执行记录</p>
            </div>
          </div>
          <EmptyState
            title="工作区活动将在这里汇集"
            description="登录后查看策略、回测与交易的最新进展。"
            icon="clock"
          />
        </section>
      </div>
    </div>
  );
}
