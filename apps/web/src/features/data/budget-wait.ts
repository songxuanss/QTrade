export type BudgetReason =
  "source_daily" | "scope_daily" | "source_concurrency" | "scope_concurrency";
const explanations: Record<BudgetReason, string> = {
  source_daily: "来源日信用不足，等待预算重置。",
  scope_daily: "当前空间日信用不足，等待预算重置。",
  source_concurrency: "来源并发预留已满，等待释放。",
  scope_concurrency: "当前空间并发预留已满，等待释放。",
};

// A missing deadline is a configuration rejection, never an invented midnight.
export function budgetWaitMessage(
  reason: BudgetReason,
  state: string,
  next: string | null,
) {
  return state === "waiting"
    ? `${explanations[reason]} 下一次尝试：${next ?? "待确认"}`
    : "预算拒绝，当前没有自动重试时间；请核对额度配置。";
}
