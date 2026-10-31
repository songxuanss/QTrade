import { redirect } from "next/navigation";
import {
  authConfig,
  currentSession,
  workspaceBacktestReport,
  workspaceBacktests,
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
  const [result, context] = await Promise.all([
    workspaceBacktestReport(runId),
    workspaceBacktests(),
  ]);
  const strategy = context.strategies.find((item) =>
    item.strategy_versions?.some(
      (candidate) =>
        candidate.strategy_version_id === result.run?.strategy_version_id,
    ),
  );
  const strategyVersion = strategy?.strategy_versions?.find(
    (candidate) =>
      candidate.strategy_version_id === result.run?.strategy_version_id,
  );
  const dataset = context.datasets.find(
    (item) => item.dataset_id === result.run?.dataset_id,
  );
  return result.run ? (
    <BacktestReport
      run={result.run}
      report={result.report}
      manifest={result.manifest}
      strategyLabel={
        strategy && strategyVersion
          ? `${strategy.name} · v${strategyVersion.version}`
          : undefined
      }
      datasetLabel={
        dataset
          ? `${dataset.instrument_ids.slice(0, 2).join(", ")} · ${dataset.granularity} · v${dataset.version}`
          : undefined
      }
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
