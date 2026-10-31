import { EmptyState } from "../../components/ui/states";
import type { WorkspaceRuntime } from "../auth/server";
import { controlRuntime } from "../../app/workspace/runtime-actions";

const stateLabel = { running: "运行中", paused: "已暂停", stopped: "已停止" };
const modeLabel = { paper: "模拟交易", live: "实盘" };
const date = (value: string) =>
  new Date(value).toLocaleString("zh-CN", { hour12: false });

function ControlForm({
  runtime,
  action,
  label,
  danger = false,
}: {
  runtime: WorkspaceRuntime;
  action: "pause" | "resume" | "stop" | "cancel_orders" | "close_positions";
  label: string;
  danger?: boolean;
}) {
  const confirmationRequired = danger;
  const phrase = `CONFIRM ${action.toUpperCase()}`;
  return (
    <form action={controlRuntime} className="runtime-control-form">
      <input type="hidden" name="runtime_id" value={runtime.runtime_id} />
      <input type="hidden" name="action" value={action} />
      {confirmationRequired && (
        <label className="runtime-confirmation">
          <span>
            输入 <code>{phrase}</code> 以确认
          </span>
          <input
            name="confirmation"
            required
            pattern={phrase}
            autoComplete="off"
            aria-label={`确认${label} ${runtime.runtime_id.slice(0, 8)}`}
          />
        </label>
      )}
      <button
        type="submit"
        className={`button ${danger ? "button-danger" : "button-secondary"}`}
      >
        {label}
      </button>
    </form>
  );
}

export function TradingConsole({
  runtimes,
  error,
  canControl,
  canClose,
  result,
}: {
  runtimes: WorkspaceRuntime[];
  error: boolean;
  canControl: boolean;
  canClose: boolean;
  result?: string;
}) {
  const active = runtimes.filter((runtime) => runtime.state !== "stopped");
  return (
    <div className="workspace-page trading-console-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">EXECUTION / CONTROL</p>
          <h1>交易执行</h1>
          <p className="page-description">
            查看运行时状态，按权限暂停、恢复或安全停止策略执行。
          </p>
        </div>
        <div className="runtime-summary" aria-label="运行时摘要">
          <strong>{active.length}</strong>
          <span>活动运行时</span>
        </div>
      </header>
      {result && (
        <p className={`runtime-flash ${result}`} role="status">
          {result === "success"
            ? "控制指令已记录，状态正在同步。"
            : "控制指令未完成，请确认权限与运行时状态。"}
        </p>
      )}
      <section className="panel runtime-safety-note">
        <span className="runtime-safety-icon" aria-hidden="true">
          ◆
        </span>
        <div>
          <h2>实盘安全门控</h2>
          <p>
            所有实盘订单都经过预交易风控。停止运行会先撤销未完成订单；执行通道不可用时，操作会被拒绝且运行状态保持不变。
          </p>
        </div>
        <span className="status-pill status-off">实盘启动需审批</span>
      </section>
      {error ? (
        <section className="panel">
          <EmptyState
            title="运行时暂不可用"
            description="无法读取工作区运行时状态。控制端不可用时，不会提交执行指令。"
          />
        </section>
      ) : runtimes.length === 0 ? (
        <section className="panel runtime-empty">
          <EmptyState
            title="暂无活动运行时"
            description="策略通过回测与审批后，可从策略版本启动模拟或实盘运行。"
          />
        </section>
      ) : (
        <section className="runtime-list" aria-label="运行时列表">
          {runtimes.map((runtime) => (
            <article className="panel runtime-card" key={runtime.runtime_id}>
              <div className="runtime-card-heading">
                <div>
                  <p className="eyebrow">{modeLabel[runtime.mode]}</p>
                  <h2>策略版本 {runtime.strategy_version_id.slice(0, 8)}</h2>
                </div>
                <span className={`runtime-state ${runtime.state}`}>
                  <i aria-hidden="true" /> {stateLabel[runtime.state]}
                </span>
              </div>
              <dl className="runtime-facts">
                <div>
                  <dt>运行时</dt>
                  <dd>{runtime.runtime_id.slice(0, 8)}</dd>
                </div>
                <div>
                  <dt>账户</dt>
                  <dd>{runtime.account_id.slice(0, 8)}</dd>
                </div>
                <div>
                  <dt>版本修订</dt>
                  <dd>r{runtime.revision}</dd>
                </div>
                <div>
                  <dt>最近更新</dt>
                  <dd>{date(runtime.updated_at)}</dd>
                </div>
              </dl>
              {canControl && runtime.state !== "stopped" && (
                <div className="runtime-controls" aria-label="运行时控制">
                  {runtime.state === "running" ? (
                    <ControlForm
                      runtime={runtime}
                      action="pause"
                      label="暂停"
                    />
                  ) : (
                    <ControlForm
                      runtime={runtime}
                      action="resume"
                      label="恢复"
                    />
                  )}
                  {runtime.order_control_available === false && (
                    <p className="runtime-control-unavailable">
                      券商订单/持仓控制尚未配置；暂停和恢复可用，撤销、平仓和停止会保持运行状态。
                    </p>
                  )}
                  {runtime.order_control_available !== false && (
                    <>
                      <ControlForm
                        runtime={runtime}
                        action="cancel_orders"
                        label="撤销未完成订单"
                        danger
                      />
                      {canClose && (
                        <ControlForm
                          runtime={runtime}
                          action="close_positions"
                          label="一键平仓"
                          danger
                        />
                      )}
                      <ControlForm
                        runtime={runtime}
                        action="stop"
                        label="停止运行"
                        danger
                      />
                    </>
                  )}
                </div>
              )}
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
