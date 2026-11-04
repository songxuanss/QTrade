import type { CollectorSource } from "./collector-types";

/** Shared collector vocabulary. Pure data and functions only, so both client
 * components and tests can import it without a "use client" boundary. */

export const observedDate = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));

export const states: Record<string, string> = {
  queued: "排队中",
  running: "采集中",
  waiting: "等待重试",
  failed: "失败",
  completed: "采集完成",
  completed_with_gaps: "完成 · 存在无数据分段",
  no_data: "无观测数据",
  cancelled: "已取消",
};
export const errors: Record<string, string> = {
  unavailable: "采集服务暂不可用，请检查独立服务或授权连接。",
  unauthorized: "来源权限验证未通过，请检查订阅和采集配置。",
  forbidden: "当前账号没有该作用域的操作权限。",
  unsupported: "该来源不支持所选市场、资产类别、周期或字段。",
  invalid_input: "请检查标的、日期范围和字段选择。",
  incomplete_dates:
    "请同时填写开始和结束日期；两个日期都留空时才采用来源默认范围。",
  default_unavailable:
    "该类别或周期的默认发布时间尚未验证，请明确填写日期后预览。",
  conflict: "计划覆盖已变化，请重新预览后提交。",
  unverified: "来源数据未通过结构或单位校验，未发布为可见行情。",
  rate_limited: "来源限流，任务会按退避计划重试。",
  timeout: "来源请求超时，任务会按退避计划重试。",
};
export const periods: Record<string, string> = {
  "1d": "日线",
  "1m": "1 分钟",
  "5m": "5 分钟",
  "15m": "15 分钟",
  "30m": "30 分钟",
  "1h": "60 分钟",
};
// Coarse-to-fine so 日线 leads the union dropdown; only periods some source of
// the current kind actually offers are rendered.
export const PERIOD_ORDER = ["1d", "1h", "30m", "15m", "5m", "1m"];

/** Paid-first default among usable sources; explicit user picks bypass this. */
export function preferredSource(
  list: CollectorSource[],
  granularity: string,
): CollectorSource | undefined {
  const usable = list.filter((item) => item.configured);
  const supporting = usable.filter((item) =>
    item.periods.includes(granularity),
  );
  return (
    supporting.find((item) => item.tier === "paid") ??
    supporting[0] ??
    usable.find((item) => item.tier === "paid") ??
    usable[0] ??
    list[0]
  );
}

export const budgetWaits: Record<string, string> = {
  source_daily: "来源日信用不足，等待预算重置；管理员调整额度后可提前重试。",
  scope_daily: "当前空间日信用不足，等待预算重置；管理员调整额度后可提前重试。",
  source_concurrency: "来源并发预留已满，等待释放后再尝试。",
  scope_concurrency: "当前空间并发预留已满，等待释放后再尝试。",
};
