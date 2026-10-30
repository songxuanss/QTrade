import Link from "next/link";
import { EmptyState, ErrorState } from "../../components/ui/states";
import {
  activateKillSwitch,
  requestRecovery,
  approveRecovery,
} from "../../app/workspace/risk-actions";
import type { workspaceRisk } from "./server";

const outcomes: Record<string, string> = {
  allow: "允许",
  deny: "拒绝",
  require_approval: "需要审批",
};

const messages: Record<string, string> = {
  success: "风控操作已记录，当前状态已更新。",
  denied: "操作被拒绝：需要相应权限，恢复审批必须由另一位授权成员完成。",
  conflict: "风控状态已变化，请刷新后确认当前状态。",
  confirmation: "请输入要求的确认文字后再提交。",
  invalid: "请填写有效的操作原因。",
  error: "风控服务暂不可用，请刷新重试。",
};

export function RiskPanel({
  data,
  canControl,
  result,
}: {
  data: Awaited<ReturnType<typeof workspaceRisk>>;
  canControl: boolean;
  result?: string;
}) {
  const { switchState, limits, decisions, recoveries, error } = data;
  return (
    <div className="workspace-page risk-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">RISK / GOVERNANCE</p>
          <h1>风控中心</h1>
          <p className="page-description">
            查看风险约束与决策，控制工作区交易门禁。
          </p>
        </div>
        <Link className="button button-secondary" href="/workspace/risk">
          刷新状态
        </Link>
      </div>
      {result && messages[result] && (
        <p
          className={`data-notice ${result === "success" ? "success" : ""}`}
          role="status"
        >
          {messages[result]}
        </p>
      )}
      {error ? (
        <ErrorState
          title="风控服务暂不可用"
          description="当前无法读取可信的风控状态，请稍后刷新。"
        />
      ) : (
        <>
          <section
            className="panel data-section"
            aria-labelledby="switch-title"
          >
            <div className="data-section-title">
              <div>
                <p className="eyebrow">WORKSPACE KILL SWITCH</p>
                <h2 id="switch-title">工作区交易安全开关</h2>
              </div>
              <span
                className={`badge ${switchState?.active ? "badge-warning" : "badge-neutral"}`}
              >
                {switchState?.active ? "已停止交易" : "未触发"}
              </span>
            </div>
            <p className="page-description">
              触发后拒绝工作区内的新交易指令。此开关不会代替撤单或平仓；恢复须由另一位管理员或风控管理员审批。
            </p>
            {switchState?.reason && (
              <p className="risk-reason">最近原因：{switchState.reason}</p>
            )}
            <p className="data-secondary">
              状态版本 {switchState?.revision ?? 0}
              {switchState?.changed_at &&
                ` · ${new Date(switchState.changed_at).toLocaleString("zh-CN")}`}
            </p>
            {canControl &&
              (switchState?.active ? (
                <form action={requestRecovery}>
                  <button className="button button-primary" type="submit">
                    申请恢复交易
                  </button>
                </form>
              ) : (
                <form action={activateKillSwitch} className="risk-control-form">
                  <label>
                    停止原因
                    <textarea
                      name="reason"
                      required
                      maxLength={500}
                      placeholder="说明需要停止新交易的原因"
                    />
                  </label>
                  <label>
                    输入“停止交易”确认
                    <input
                      name="confirmation"
                      required
                      pattern="停止交易"
                      autoComplete="off"
                    />
                  </label>
                  <button className="button button-secondary" type="submit">
                    触发交易安全开关
                  </button>
                </form>
              ))}
            {!canControl && (
              <p className="data-secondary">
                仅管理员和风控管理员可操作交易安全开关。
              </p>
            )}
          </section>
          <section
            className="panel data-section"
            aria-labelledby="recovery-title"
          >
            <div className="data-section-title">
              <div>
                <p className="eyebrow">INDEPENDENT APPROVAL</p>
                <h2 id="recovery-title">待审批恢复申请</h2>
              </div>
              <span>{recoveries.length} 项</span>
            </div>
            {!recoveries.length ? (
              <p className="data-empty">暂无恢复申请。</p>
            ) : (
              recoveries.map((item) => (
                <article className="risk-recovery" key={item.request_id}>
                  <strong>
                    {item.account_id
                      ? `账户 ${item.account_id}`
                      : "工作区全局恢复"}
                  </strong>
                  <p className="data-secondary">
                    {new Date(item.requested_at).toLocaleString("zh-CN")} ·{" "}
                    {item.request_id}
                  </p>
                  {item.self_requested ? (
                    <p className="data-secondary">
                      你是申请人，请由另一位授权成员审批。
                    </p>
                  ) : (
                    canControl && (
                      <form
                        action={approveRecovery}
                        className="risk-control-form"
                      >
                        <input
                          type="hidden"
                          name="request_id"
                          value={item.request_id}
                        />
                        <label>
                          审批原因
                          <textarea name="reason" required maxLength={500} />
                        </label>
                        <label>
                          输入“批准恢复”确认
                          <input
                            name="confirmation"
                            required
                            pattern="批准恢复"
                            autoComplete="off"
                          />
                        </label>
                        <button className="button button-primary" type="submit">
                          批准恢复交易
                        </button>
                      </form>
                    )
                  )}
                </article>
              ))
            )}
          </section>
          <section
            className="panel data-section"
            aria-labelledby="limits-title"
          >
            <div className="data-section-title">
              <div>
                <p className="eyebrow">PRE-TRADE CONSTRAINTS</p>
                <h2 id="limits-title">已配置风险限额</h2>
              </div>
              <span>{limits.length} 项</span>
            </div>
            {!limits.length ? (
              <p className="data-empty">
                尚无额外配置的风险限额。新订单仍须通过权限、策略状态、市场状态和安全开关等基础预交易检查。
              </p>
            ) : (
              <div className="risk-table-scroll">
                <table className="risk-table">
                  <thead>
                    <tr>
                      <th>范围</th>
                      <th>指标</th>
                      <th>上限</th>
                      <th>版本</th>
                    </tr>
                  </thead>
                  <tbody>
                    {limits.map((item) => (
                      <tr key={item.limit_id}>
                        <td>
                          {item.dimension} · {item.scope_id}
                        </td>
                        <td>{item.metric}</td>
                        <td>{item.maximum}</td>
                        <td>v{item.version}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section
            className="panel data-section"
            aria-labelledby="decisions-title"
          >
            <div className="data-section-title">
              <div>
                <p className="eyebrow">DECISION HISTORY</p>
                <h2 id="decisions-title">风险决策记录</h2>
              </div>
              <span>{decisions.length} 项</span>
            </div>
            {!decisions.length ? (
              <EmptyState
                title="暂无风险决策"
                description="交易指令经过预交易检查后，决策与原因会显示在这里。"
              />
            ) : (
              <div className="data-list">
                {decisions.map(({ decision }) => (
                  <article className="data-row" key={decision.decision_id}>
                    <div className="data-row-main">
                      <strong>
                        {outcomes[decision.outcome] ?? decision.outcome}
                      </strong>
                      <span className="data-secondary">
                        {new Date(decision.decided_at).toLocaleString("zh-CN")}
                      </span>
                      <span>
                        {decision.reasons
                          .map((reason) => reason.message)
                          .join("；")}
                      </span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
