import { redirect } from "next/navigation";
import {
  authConfig,
  currentSession,
  workspaceBacktestReport,
} from "../../../../features/auth/server";
import { BacktestReport } from "../../../../features/backtesting/report";

export default async function BacktestReportPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const [{ runId }, configured, session] = await Promise.all([
    params,
    Promise.resolve(Boolean(authConfig())),
    currentSession(),
  ]);
  if (configured && !session)
    redirect(
      `/auth/login?returnTo=${encodeURIComponent(`/workspace/backtests/${runId}`)}`,
    );
  const result = await workspaceBacktestReport(runId);
  return result.run ? (
    <BacktestReport
      run={result.run}
      report={result.report}
      manifest={result.manifest}
    />
  ) : (
    <div className="workspace-page">
      <section className="panel backtest-report-unavailable">
        <p className="eyebrow">RUN REPORT</p>
        <h1>无法读取回测报告</h1>
        <p>运行记录不存在、当前成员无权查看，或回测服务暂不可用。</p>
      </section>
    </div>
  );
}
