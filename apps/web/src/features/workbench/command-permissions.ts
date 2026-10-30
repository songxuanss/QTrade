import type { PublicWorkspace } from "../auth/public";
import { navigation } from "./navigation";

export function permittedPages(role: PublicWorkspace["role"]) {
  const allowed = new Set<string>(["/workspace/market", "/workspace/data"]);
  if (["analyst", "admin"].includes(role)) {
    allowed.add("/workspace/strategies");
    allowed.add("/workspace/backtests");
  }
  if (["trader", "admin"].includes(role)) allowed.add("/workspace/trading");
  if (["analyst", "trader", "risk_manager", "admin"].includes(role))
    allowed.add("/workspace/alerts");
  if (["risk_manager", "admin"].includes(role)) allowed.add("/workspace/risk");
  if (role === "admin") allowed.add("/workspace/settings");
  return navigation.filter((item) => allowed.has(item.href));
}

const pageTerms: Record<string, string[]> = {
  "/workspace/market": ["instrument", "标的", "market watch", "行情"],
  "/workspace/data": ["data batch", "dataset", "数据批次", "数据"],
  "/workspace/strategies": ["strategy", "strategy version", "策略"],
  "/workspace/backtests": ["backtest", "backtest run", "回测"],
  "/workspace/trading": ["order", "orders", "订单", "交易"],
  "/workspace/alerts": ["alert", "alerts", "告警"],
  "/workspace/risk": ["risk", "风控"],
  "/workspace/settings": ["settings", "adapter", "broker", "设置", "能力"],
};

export function searchablePages(role: PublicWorkspace["role"], query: string) {
  const term = query.trim().toLocaleLowerCase();
  if (!term) return [];
  return permittedPages(role).filter((page) =>
    [page.label, page.href, ...(pageTerms[page.href] ?? [])].some((value) =>
      value.toLocaleLowerCase().includes(term),
    ),
  );
}
