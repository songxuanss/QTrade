import type { IconName } from "../../components/ui/icon";

export const navigation: {
  href: string;
  label: string;
  icon: IconName;
  group: "研究与交易" | "治理与管理";
}[] = [
  { href: "/", label: "工作台", icon: "grid", group: "研究与交易" },
  {
    href: "/workspace/market",
    label: "市场终端",
    icon: "market",
    group: "研究与交易",
  },
  {
    href: "/workspace/strategies",
    label: "策略研究",
    icon: "strategy",
    group: "研究与交易",
  },
  {
    href: "/workspace/backtests",
    label: "回测实验",
    icon: "backtest",
    group: "研究与交易",
  },
  {
    href: "/workspace/trading",
    label: "交易执行",
    icon: "trade",
    group: "研究与交易",
  },
  {
    href: "/workspace/risk",
    label: "风控中心",
    icon: "shield",
    group: "治理与管理",
  },
  {
    href: "/workspace/alerts",
    label: "告警中心",
    icon: "bell",
    group: "治理与管理",
  },
  {
    href: "/workspace/data",
    label: "数据管理",
    icon: "data",
    group: "治理与管理",
  },
  {
    href: "/workspace/health",
    label: "系统健康",
    icon: "activity",
    group: "治理与管理",
  },
  {
    href: "/workspace/audit",
    label: "审计记录",
    icon: "shield",
    group: "治理与管理",
  },
  {
    href: "/workspace/settings",
    label: "工作区设置",
    icon: "settings",
    group: "治理与管理",
  },
];
