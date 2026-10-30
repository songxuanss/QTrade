export const ROLE_NAMES = {
  viewer: "只读成员",
  analyst: "策略分析师",
  trader: "交易员",
  risk_manager: "风险管理员",
  admin: "工作区管理员",
} as const;

export interface PublicWorkspace {
  name: string;
  role: keyof typeof ROLE_NAMES;
}
