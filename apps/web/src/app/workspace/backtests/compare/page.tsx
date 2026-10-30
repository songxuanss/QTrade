import { redirect } from "next/navigation";
import {
  authConfig,
  currentSession,
  workspaceBacktestComparison,
} from "../../../../features/auth/server";
import { BacktestComparison } from "../../../../features/backtesting/comparison";

export default async function BacktestComparisonPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [query, configured, session] = await Promise.all([
    searchParams,
    Promise.resolve(Boolean(authConfig())),
    currentSession(),
  ]);
  if (configured && !session)
    redirect(
      `/auth/login?returnTo=${encodeURIComponent("/workspace/backtests")}`,
    );
  const runIds = query.run_id;
  const ids = Array.isArray(runIds) ? runIds : runIds ? [runIds] : [];
  const { comparison } = await workspaceBacktestComparison(ids);
  return comparison ? (
    <BacktestComparison comparison={comparison} />
  ) : (
    <div className="workspace-page">
      <section className="panel backtest-report-unavailable">
        <p className="eyebrow">EXPERIMENT COMPARISON</p>
        <h1>无法读取回测对比</h1>
        <p>请传入 2 至 20 个当前工作区内已完成的回测运行。</p>
      </section>
    </div>
  );
}
