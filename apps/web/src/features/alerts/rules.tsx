import type { AlertRule } from "./rules-server";
import {
  createAlertRule,
  toggleAlertRule,
  evaluateAlertRules,
} from "../../app/workspace/alert-rule-actions";
const sources: Record<string, string> = {
  market: "市场",
  risk: "风险",
  data: "数据",
  system: "系统",
};
const severities: Record<string, string> = {
  info: "提示",
  warning: "警告",
  critical: "严重",
};
function SourceOptions() {
  return Object.entries(sources).map(([value, label]) => (
    <option key={value} value={value}>
      {label}
    </option>
  ));
}
export function AlertRules({
  rules,
  error,
  canWrite,
  result,
}: {
  rules: AlertRule[];
  error: boolean;
  canWrite: boolean;
  result?: string;
}) {
  const notices: Record<string, string> = {
    success: "规则已保存",
    queued: "评估已提交。后台处理完成后刷新查看告警；未满足条件不会产生事件。",
    invalid: "输入不符合要求，请检查条件表达式和参数。",
    denied: "当前成员没有修改告警规则的权限。",
    conflict: "规则已变化，请刷新后重试。",
    error: "告警规则服务暂不可用，请稍后重试。",
  };
  return (
    <section className="panel alert-rules-panel" aria-label="告警规则">
      <div className="alert-list-heading">
        <div>
          <h2>告警规则</h2>
          <p className="page-description">
            配置触发条件和冷却时间，通知发送至工作区站内告警。
          </p>
        </div>
        <span>{rules.length} 条规则</span>
      </div>
      {result && (
        <p
          className={`alert-flash ${["success", "queued"].includes(result) ? "success" : "error"}`}
          role="status"
        >
          {notices[result] ?? notices.error}
        </p>
      )}
      {error ? (
        <p role="alert">无法载入规则，请稍后重试。</p>
      ) : rules.length ? (
        <div className="risk-table-scroll">
          <table className="risk-table">
            <thead>
              <tr>
                <th>规则</th>
                <th>条件</th>
                <th>级别 / 冷却</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.rule_id}>
                  <td>
                    <strong>{rule.name}</strong>
                    <small>
                      {sources[rule.source]} · v{rule.version}
                    </small>
                  </td>
                  <td>
                    <code>{rule.condition}</code>
                  </td>
                  <td>
                    {severities[rule.severity]} / {rule.cooldown_seconds} 秒
                  </td>
                  <td>
                    {canWrite ? (
                      <form action={toggleAlertRule}>
                        <input
                          type="hidden"
                          name="rule_id"
                          value={rule.rule_id}
                        />
                        <input
                          type="hidden"
                          name="enabled"
                          value={String(!rule.enabled)}
                        />
                        <button
                          className="button button-secondary"
                          aria-label={`${rule.enabled ? "停用" : "启用"}规则 ${rule.name}`}
                        >
                          {rule.enabled ? "停用" : "启用"}
                        </button>
                      </form>
                    ) : rule.enabled ? (
                      "已启用"
                    ) : (
                      "已停用"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="alert-history-empty">
          尚无规则。配置规则后，对接的事件评估会生成告警。
        </p>
      )}
      {canWrite && !error && (
        <>
          <details className="alert-rule-editor">
            <summary>创建告警规则</summary>
            <form action={createAlertRule} className="alert-rule-form">
              <label>
                规则名称
                <input
                  name="name"
                  required
                  maxLength={120}
                  placeholder="例如：价格越过阈值"
                />
              </label>
              <label>
                事件来源
                <select name="source">
                  <SourceOptions />
                </select>
              </label>
              <label>
                告警级别
                <select name="severity" defaultValue="warning">
                  {Object.entries(severities).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                冷却时间（秒）
                <input
                  name="cooldown_seconds"
                  type="number"
                  min={0}
                  max={86400}
                  defaultValue={60}
                  required
                />
              </label>
              <label className="alert-rule-wide">
                触发条件
                <input
                  name="condition"
                  aria-label="触发条件"
                  aria-describedby="alert-condition-help"
                  required
                  maxLength={512}
                  placeholder="price > 100"
                />
                <small id="alert-condition-help">
                  支持数值比较及 and / or / not；字段必须来自事件事实，例如
                  price &gt; 100。
                </small>
              </label>
              <button className="button button-primary" type="submit">
                保存告警规则
              </button>
            </form>
          </details>
          <details className="alert-rule-editor">
            <summary>手动评估规则</summary>
            <p className="page-description">
              使用你输入的事实触发当前已启用规则，命中会生成真实告警。此操作不读取实时行情。
            </p>
            <form className="alert-rule-form" action={evaluateAlertRules}>
              <label>
                评估来源
                <select name="source">
                  <SourceOptions />
                </select>
              </label>
              <label className="alert-rule-wide">
                事件事实（JSON）
                <textarea
                  name="facts"
                  required
                  rows={3}
                  maxLength={8192}
                  placeholder={'{"price": 101}'}
                />
              </label>
              <button className="button button-secondary" type="submit">
                提交规则评估
              </button>
            </form>
          </details>
        </>
      )}
    </section>
  );
}
