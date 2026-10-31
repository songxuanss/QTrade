import { Suspense } from "react";
import { DistributionCollectionPanel } from "../../../features/data/distribution-collection-panel";
import { ResearchCollectionPanel } from "../../../features/data/research-collection-panel";
import { CollectorPanel } from "../../../features/data/collector-panel";
import { SchedulePanel } from "../../../features/data/schedule-panel";
import { FreshnessHealthPanel } from "../../../features/data/freshness-health-panel";
import { SourceProfilePanel } from "../../../features/data/source-profile-panel";
import { CollectorUsagePanel } from "../../../features/data/collector-usage-panel";
import { readSession } from "../../../features/auth/session-access";
import {
  workspaceCollector,
  workspaceResearchCollection,
  workspaceInstrumentNames,
} from "../../../features/data/collector-server";
import { UniversePanel } from "../../../features/data/universe-panel";
import {
  workspaceUniverseCatalog,
  workspaceUniverseSnapshot,
  workspaceUniverseSnapshots,
} from "../../../features/data/universe-server";
import { AuditPanel } from "../../../features/audit/panel";
import {
  workspaceAudit,
  type AuditFilters,
} from "../../../features/audit/server";
import { workspaceDatasetQuality } from "../../../features/data/research-server";
import { notFound, redirect } from "next/navigation";
import {
  authConfig,
  currentIdentity,
  currentSession,
  workspaceMarketInstruments,
  workspaceMarketStream,
  workspaceData,
  workspaceHealthSummary,
  workspaceAlerts,
  workspaceStrategies,
  workspaceBacktests,
  workspaceRuntimes,
} from "../../../features/auth/server";
import { EmptyState, PermissionState } from "../../../components/ui/states";
import { navigation } from "../../../features/workbench/navigation";
import { CapabilityPanel } from "../../../features/capabilities/panel";
import { DataWorkbench } from "../data-workbench";
import { MarketWatch } from "../../../features/market/watchlist";
import { HealthPanel } from "../../../features/workbench/health-panel";
import { workspaceAlertRules } from "../../../features/alerts/rules-server";
import { AlertCenter } from "../../../features/alerts/center";
import { StrategyWorkbench } from "../../../features/strategies/workbench";
import { BacktestWorkbench } from "../../../features/backtesting/workbench";
import { TradingConsole } from "../../../features/trading/console";
import { workspaceResearchDatasets } from "../../../features/data/research-server";
import { RiskPanel } from "../../../features/risk/panel";
import { workspaceRisk } from "../../../features/risk/server";

/** The reference catalog can fall back to a slow provider call. Keep it in its
 * own boundary so the downloaded-data and collection panels are not blocked. */
async function UniverseSection({
  search,
  category,
  offset,
  memberPage,
  result,
  canManage,
  periods,
  recent,
  snapshotId,
}: {
  search: string;
  category: string;
  offset: number;
  memberPage: number;
  result?: string;
  canManage: boolean;
  periods: string[];
  recent: Awaited<ReturnType<typeof workspaceUniverseSnapshots>>;
  snapshotId?: string;
}) {
  const [catalog, snapshot] = await Promise.all([
    workspaceUniverseCatalog(search, category, offset),
    snapshotId ? workspaceUniverseSnapshot(snapshotId) : Promise.resolve(null),
  ]);
  return (
    <UniversePanel
      catalog={catalog}
      periods={periods}
      snapshot={snapshot}
      recent={recent}
      canManage={canManage}
      search={search}
      category={category}
      offset={offset}
      memberPage={memberPage}
      result={result}
    />
  );
}

export default async function WorkspaceSection({
  params,
  searchParams,
}: {
  params: Promise<{ section: string }>;
  searchParams: Promise<{
    start?: string;
    end?: string;
    actor_id?: string;
    action?: string;
    target_id?: string;
    trace_id?: string;
    before?: string;
    result?: string;
    rules_result?: string;
    instrument?: string;
    granularity?: string;
    dataset_id?: string;
    event_id?: string;
    strategy_id?: string;
    quality_dataset?: string;
    universe_search?: string;
    universe_category?: string;
    universe_offset?: string;
    universe_snapshot?: string;
    universe_member_page?: string;
    universe_result?: string;
    download_source?: string;
    download_granularity?: string;
    download_instrument?: string;
    download_start?: string;
    download_end?: string;
    data_view?: string;
    collect_view?: string;
    research_view?: string;
    asset_view?: string;
    monitor_view?: string;
  }>;
}) {
  const [{ section }, query] = await Promise.all([params, searchParams]);
  const item = navigation.find((item) => item.href === `/workspace/${section}`);
  if (!item) notFound();
  const configured = Boolean(authConfig());
  const authenticated = configured && (await currentSession());
  if (configured && !authenticated) {
    // Keep deep workspace navigation through SSO without accepting a caller-
    // supplied redirect target. Draft/provider data never enters this return URL.
    const navigationQuery = new URLSearchParams();
    if (section === "data") {
      for (const key of [
        "data_view",
        "collect_view",
        "research_view",
        "asset_view",
        "monitor_view",
      ] as const)
        if (query[key]) navigationQuery.set(key, query[key].slice(0, 40));
    }
    const target =
      item.href + (navigationQuery.size ? `?${navigationQuery}` : "");
    redirect("/auth/login?returnTo=" + encodeURIComponent(target));
  }
  if (section === "audit" && authenticated) {
    const identity = await currentIdentity();
    const allowed = ["admin", "risk_manager"].includes(
      identity.workspace?.role ?? "",
    );
    const filters: AuditFilters = {
      start: query.start,
      end: query.end,
      actor_id: query.actor_id,
      action: query.action,
      target_id: query.target_id,
      result: query.result,
      trace_id: query.trace_id,
      before: query.before,
    };
    for (const key of ["start", "end"] as const)
      if (filters[key] && !/[zZ]|[+-]\d{2}:\d{2}$/.test(filters[key]))
        filters[key] += "Z";
    return (
      <AuditPanel
        allowed={allowed}
        filters={filters}
        data={allowed ? await workspaceAudit(filters) : null}
      />
    );
  }
  if (section === "data" && authenticated) {
    const search = query.universe_search ?? "";
    const category = query.universe_category ?? "";
    const offset = Math.max(
      0,
      Math.min(Number(query.universe_offset) || 0, 10000),
    );
    const memberPage = Math.max(
      0,
      Math.min(Number(query.universe_member_page) || 0, 1000),
    );
    const [
      identity,
      data,
      research,
      recent,
      collector,
      researchCollection,
      distributions,
    ] = await Promise.all([
      currentIdentity(),
      workspaceData(),
      workspaceResearchDatasets(),
      workspaceUniverseSnapshots(),
      workspaceCollector(),
      workspaceResearchCollection(),
      workspaceResearchCollection("corporate_action"),
    ]);
    const instrumentNames = await workspaceInstrumentNames([
      ...distributions.jobs.flatMap((job) => job.request.instrument_ids),
      ...researchCollection.jobs.flatMap((job) => job.request.instrument_ids),
      ...(data?.tasks ?? []).map((item) => item.instrument_id),
      ...(data?.batches ?? []).map((item) => item.instrument_id),
    ]);
    const quality = query.quality_dataset
      ? await workspaceDatasetQuality(query.quality_dataset)
      : null;
    const usageScope = (await readSession())?.workspaceId;
    return (
      <DataWorkbench
        instrumentNames={instrumentNames}
        initialView={
          query.data_view ??
          (query.universe_snapshot !== undefined ||
          query.universe_search !== undefined ||
          query.universe_category !== undefined ||
          query.universe_offset !== undefined ||
          query.universe_result !== undefined
            ? "pools"
            : query.quality_dataset || query.result?.startsWith("research_")
              ? "assets"
              : query.download_instrument || query.result
                ? "ticks"
                : "collect")
        }
        initialResearchView={query.research_view}
        initialAssetView={
          query.asset_view ?? (query.quality_dataset ? "quality" : "research")
        }
        initialMonitorView={query.monitor_view}
        collectorPanel={
          <CollectorPanel
            {...collector}
            initialJobs={collector.jobs}
            snapshots={recent}
            canManage={identity.workspace?.role === "admin"}
          />
        }
        schedulePanel={
          <SchedulePanel
            key={`schedule-${usageScope}`}
            snapshots={recent}
            names={instrumentNames}
            canManage={identity.workspace?.role === "admin"}
          />
        }
        financialPanel={
          <ResearchCollectionPanel
            {...researchCollection}
            initialJobs={researchCollection.jobs}
            instrumentNames={instrumentNames}
            canManage={identity.workspace?.role === "admin"}
          />
        }
        distributionPanel={
          <DistributionCollectionPanel
            {...distributions}
            initialJobs={distributions.jobs}
            instrumentNames={instrumentNames}
            canManage={identity.workspace?.role === "admin"}
          />
        }
        healthPanel={<FreshnessHealthPanel key={`health-${usageScope}`} />}
        usagePanel={<CollectorUsagePanel key={usageScope ?? "usage"} />}
        sourceProfilePanel={
          <SourceProfilePanel key={`profiles-${usageScope}`} />
        }
        universePanel={
          <Suspense
            fallback={
              <div className="panel data-section">
                <p className="data-empty">正在加载股票池目录…</p>
              </div>
            }
          >
            <UniverseSection
              search={search}
              category={category}
              offset={offset}
              memberPage={memberPage}
              result={query.universe_result}
              canManage={identity.workspace?.role === "admin"}
              periods={[
                ...new Set(
                  (data?.sourceOptions ?? []).flatMap(
                    (item) => item.granularities,
                  ),
                ),
              ]}
              recent={recent}
              snapshotId={query.universe_snapshot}
            />
          </Suspense>
        }
        research={research}
        sourceOptions={data?.sourceOptions ?? []}
        qualityReport={quality}
        showQuality={Boolean(query.quality_dataset)}
        tasks={data?.tasks ?? []}
        batches={data?.batches ?? []}
        issues={data?.issues ?? []}
        sources={data?.sources ?? []}
        maxDownloadDays={data?.maxDownloadDays ?? null}
        error={data?.error ?? true}
        canManage={identity.workspace?.role === "admin"}
        result={query.result}
        downloadRequest={{
          source: query.download_source ?? "mairui.a-share.tick",
          granularity: query.download_granularity ?? "tick",
          instrument: query.download_instrument ?? "",
          start: query.download_start ?? "",
          end: query.download_end ?? "",
        }}
      />
    );
  }
  if (section === "risk" && authenticated) {
    const [identity, data] = await Promise.all([
      currentIdentity(),
      workspaceRisk(),
    ]);
    return (
      <RiskPanel
        data={data}
        canControl={["admin", "risk_manager"].includes(
          identity.workspace?.role ?? "",
        )}
        result={query.result}
      />
    );
  }
  if (section === "market" && authenticated) {
    const catalog = await workspaceMarketInstruments(query.instrument);
    const stream = await workspaceMarketStream();
    return (
      <MarketWatch
        instruments={catalog?.instruments ?? []}
        datasets={catalog?.datasets ?? []}
        datasetsError={catalog?.datasetsError ?? true}
        catalogError={catalog?.error ?? true}
        stream={stream}
        initialInstrumentId={query.instrument}
        initialGranularity={query.granularity}
        initialDatasetId={query.dataset_id}
      />
    );
  }
  if (section === "health" && authenticated) {
    const health = await workspaceHealthSummary();
    return <HealthPanel summary={health} />;
  }
  if (section === "alerts" && authenticated) {
    const [identity, alerts, rules] = await Promise.all([
      currentIdentity(),
      workspaceAlerts(query.event_id),
      workspaceAlertRules(),
    ]);
    return (
      <AlertCenter
        rules={rules.rules}
        rulesError={rules.error}
        rulesResult={query.rules_result}
        canWriteRules={["admin", "analyst"].includes(
          identity.workspace?.role ?? "",
        )}
        events={alerts.events}
        selected={alerts.selected}
        history={alerts.history}
        error={alerts.error}
        result={query.result}
        canHandle={["admin", "analyst", "trader", "risk_manager"].includes(
          identity.workspace?.role ?? "",
        )}
      />
    );
  }
  if (section === "strategies" && authenticated) {
    const [identity, data] = await Promise.all([
      currentIdentity(),
      workspaceStrategies(query.strategy_id),
    ]);
    return (
      <StrategyWorkbench
        key={query.strategy_id ?? "new"}
        strategies={data.strategies}
        selected={data.selected}
        versions={data.versions}
        lifecycle={data.lifecycle}
        code={data.code}
        dependencies={data.dependencies}
        error={data.error}
        canEdit={["admin", "analyst"].includes(identity.workspace?.role ?? "")}
        result={query.result}
      />
    );
  }
  if (section === "backtests" && authenticated) {
    const [identity, data] = await Promise.all([
      currentIdentity(),
      workspaceBacktests(),
    ]);
    return (
      <BacktestWorkbench
        runs={data.runs}
        datasets={data.datasets}
        strategies={data.strategies}
        memberships={data.memberships}
        error={data.error}
        canRun={["admin", "analyst"].includes(identity.workspace?.role ?? "")}
      />
    );
  }
  if (section === "trading" && authenticated) {
    const [identity, data] = await Promise.all([
      currentIdentity(),
      workspaceRuntimes(),
    ]);
    const role = identity.workspace?.role ?? "";
    return (
      <TradingConsole
        runtimes={data.runtimes}
        error={data.error}
        canControl={["admin", "analyst", "trader"].includes(role)}
        canClose={["admin", "trader"].includes(role)}
        result={query.result}
      />
    );
  }
  return (
    <div className="workspace-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">WORKSPACE</p>
          <h1>{item.label}</h1>
          <p className="page-description">工作区资源仅对授权成员开放。</p>
        </div>
      </div>
      <section className="panel section-permission">
        {query.result === "workspace_denied" && (
          <p role="alert" className="data-notice">
            无法切换工作区，请确认成员权限或重新登录。
          </p>
        )}
        {authenticated && section === "settings" ? (
          <CapabilityPanel />
        ) : authenticated ? (
          <EmptyState
            headingLevel={2}
            title="功能建设中"
            description="工作区已连接，该模块将在验收后开放。"
          />
        ) : (
          <PermissionState />
        )}
      </section>
    </div>
  );
}
