import { workspaceAdapters } from "../auth/server";
import { EmptyState, ErrorState } from "../../components/ui/states";

type Descriptor = {
  adapter_id: string;
  name: string;
  kind: string;
  health: string;
  markets: string[];
  granularities: string[];
  order_types: string[];
  trading_sessions: string[];
  live: boolean;
};

const kinds: Record<string, string> = {
  engine: "交易引擎",
  market_data: "行情数据",
  broker: "交易通道",
  research: "研究工具",
  object_store: "对象存储",
  notification: "通知通道",
};
const health: Record<string, string> = {
  healthy: "正常",
  degraded: "已降级",
  unavailable: "不可用",
  unknown: "尚未确认",
};

function descriptors(value: unknown): Descriptor[] {
  if (!Array.isArray(value) || value.length > 100)
    throw Error("Invalid capabilities");
  for (const item of value) {
    if (
      !item ||
      typeof item !== "object" ||
      item.version !== 1 ||
      typeof item.adapter_id !== "string" ||
      !/^[a-z][a-z0-9_-]{0,63}$/.test(item.adapter_id) ||
      typeof item.name !== "string" ||
      !item.name ||
      item.name.length > 128 ||
      !Object.hasOwn(kinds, item.kind) ||
      !Object.hasOwn(health, item.health) ||
      typeof item.live !== "boolean" ||
      ["markets", "granularities", "order_types", "trading_sessions"].some(
        (key) =>
          !Array.isArray(item[key]) ||
          item[key].length > 100 ||
          item[key].some(
            (entry: unknown) => typeof entry !== "string" || entry.length > 64,
          ),
      )
    )
      throw Error("Invalid capabilities");
  }
  return value;
}

export async function CapabilityPanel() {
  let items: Descriptor[];
  try {
    items = descriptors(await workspaceAdapters());
  } catch {
    return (
      <ErrorState
        title="能力状态暂时不可用"
        description="请稍后刷新工作区设置。"
      />
    );
  }
  if (!items.length)
    return (
      <EmptyState
        headingLevel={2}
        title="尚未配置适配器"
        description="配置行情、引擎或交易通道后，可在这里查看能力与健康状态。"
      />
    );
  return (
    <div className="capability-list">
      <div className="capability-intro">
        <p className="eyebrow">ADAPTER CAPABILITIES</p>
        <h2>连接能力</h2>
        <p>能力由当前连接声明；未支持的功能不可用。</p>
      </div>
      {items.map((item) => (
        <article className="capability-card" key={item.adapter_id}>
          <header>
            <div>
              <p className="eyebrow">{kinds[item.kind]}</p>
              <h3>{item.name}</h3>
            </div>
            <span className="badge badge-neutral">{health[item.health]}</span>
          </header>
          <dl>
            {(
              [
                ["市场", item.markets],
                ["数据周期", item.granularities],
                ["订单类型", item.order_types],
                ["交易时段", item.trading_sessions],
              ] as [string, string[]][]
            ).map(([label, values]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{values.length ? values.join(" · ") : "不支持"}</dd>
              </div>
            ))}
          </dl>
          <p className="capability-status">
            实盘交易：
            {item.live && item.health === "healthy"
              ? "支持（仍需权限与风控审批）"
              : "不可用"}
          </p>
          {item.health !== "healthy" && (
            <p className="capability-status">
              连接尚未恢复正常，当前能力不可执行。
            </p>
          )}
        </article>
      ))}
    </div>
  );
}
