import Link from "next/link";
import type { WorkspaceHealthSummary } from "../auth/server";

function stateLabel(value: string) {
  const labels: Record<string, string> = {
    healthy: "正常",
    connected: "已连接",
    degraded: "降级",
    disconnected: "未连接",
    unavailable: "不可用",
    error: "异常",
  };
  return labels[value.toLowerCase()] ?? value;
}

function Status({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const tone =
    normalized === "healthy" || normalized === "connected"
      ? "good"
      : normalized === "degraded"
        ? "warn"
        : "bad";
  return <span className={`health-status ${tone}`}>{stateLabel(value)}</span>;
}

export function HealthPanel({
  summary,
}: {
  summary: WorkspaceHealthSummary | null;
}) {
  return (
    <div className="workspace-page health-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">OPERATIONS</p>
          <h1>系统与数据健康</h1>
          <p className="page-description">
            集中检查采集任务、数据质量、行情流与交易连接器状态。
          </p>
        </div>
        <Link
          className="button-secondary health-refresh"
          href="/workspace/health"
        >
          刷新状态
        </Link>
      </div>
      {!summary || summary.error ? (
        <div className="health-notice" role="status">
          <strong>部分健康数据不可用</strong>
          <span>
            无法读取的来源会单独显示为不可用；请检查 API 与工作区连接。
          </span>
        </div>
      ) : null}
      <section className="health-metrics" aria-label="数据任务摘要">
        <Link className="panel health-metric" href="/workspace/data#collection">
          <span>采集失败</span>
          <strong>{summary?.collectionFailures ?? "—"}</strong>
          <small>查看采集任务</small>
        </Link>
        <Link className="panel health-metric" href="/workspace/data#collection">
          <span>待处理队列</span>
          <strong>{summary?.queueBacklog ?? "—"}</strong>
          <small>查看任务队列</small>
        </Link>
        <Link className="panel health-metric" href="/workspace/data#quality">
          <span>质量问题</span>
          <strong>{summary?.qualityIssues ?? "—"}</strong>
          <small>查看质量检查</small>
        </Link>
      </section>
      <div className="health-columns">
        <section className="panel health-card" aria-labelledby="health-streams">
          <div className="health-card-heading">
            <div>
              <p className="eyebrow">MARKET DATA</p>
              <h2 id="health-streams">行情流</h2>
            </div>
            <Link href="/workspace/market">打开市场终端 ↗</Link>
          </div>
          {!summary || !summary.streamsAvailable ? (
            <p className="health-empty">行情流状态不可用</p>
          ) : summary.streams.length === 0 ? (
            <p className="health-empty">尚未发现行情流状态。</p>
          ) : (
            <ul className="health-list">
              {summary.streams.map((stream) => (
                <li key={`${stream.source}:${stream.instrument_id}`}>
                  <div>
                    <strong>{stream.source}</strong>
                    <span>{stream.instrument_id}</span>
                  </div>
                  <div className="health-row-state">
                    <Status value={stream.status} />
                    {stream.latest_lag_ms !== null ? (
                      <small>
                        延迟 {stream.latest_lag_ms.toLocaleString()} ms
                      </small>
                    ) : (
                      <small>延迟未知</small>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="panel health-card" aria-labelledby="health-brokers">
          <div className="health-card-heading">
            <div>
              <p className="eyebrow">EXECUTION</p>
              <h2 id="health-brokers">交易连接器</h2>
            </div>
            <Link href="/workspace/settings">连接设置 ↗</Link>
          </div>
          {!summary || !summary.brokersAvailable ? (
            <p className="health-empty">连接器状态不可用</p>
          ) : summary.brokers.length === 0 ? (
            <p className="health-empty">尚未配置交易连接器。</p>
          ) : (
            <ul className="health-list">
              {summary.brokers.map((broker) => (
                <li key={broker.adapter_id}>
                  <div>
                    <strong>{broker.name}</strong>
                    <span>{broker.adapter_id}</span>
                  </div>
                  <Status value={broker.health} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
