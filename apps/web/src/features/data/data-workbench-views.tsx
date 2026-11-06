"use client";

import type { ComponentProps, ReactNode } from "react";
import { CollectorPanel } from "./collector-panel";
import scopeStyles from "./collector-scope.module.css";
import { WorkbenchTabs, type PanelAliases } from "./workbench-tabs";
import { MobileSelectionBar, WorkbenchScopeBar } from "./workbench-scope-bar";

type CollectorPanelProps = Omit<
  ComponentProps<typeof CollectorPanel>,
  "children"
>;

/** Legacy data_view values must keep old bookmarks, SSO returnTo targets and
 * in-app links working after the four-view restructure. `collect` resolves
 * conditionally because it owned both create and results halves. */
const viewAliases: PanelAliases = {
  pools: "catalog",
  fundamentals: "download",
  ticks: "download",
  schedules: "sources",
  monitor: "sources",
  assets: "jobs",
  collect: (url) => {
    const query = url.searchParams;
    return query.get("collect_view") === "results" ||
      (query.has("collector_job") && query.get("collect_view") !== "create")
      ? "jobs"
      : "download";
  },
};

const viewAnchors: Record<string, string> = {
  "independent-collector": "download",
  "financial-collection": "download",
  "distribution-collection": "download",
  "legacy-downloads": "download",
  "pool-download": "catalog",
  "universe-catalog": "catalog",
  "universe-members": "catalog",
  collection: "jobs",
  batches: "jobs",
  quality: "jobs",
  "research-data": "jobs",
  composition: "jobs",
};

/** Client-side assembly of the four task-flow views. The collector panel is
 * instantiated once here and its create/results halves are distributed to the
 * download/jobs views through the render-prop API, so both halves stay
 * mounted (drafts, polling and backoff survive view switches) while the URL
 * stays the single navigation source of truth. */
export function DataWorkbenchViews({
  collectorProps,
  universePanel,
  entriesTabs,
  assetsContent,
  sourcesTabs,
  initialView,
}: {
  collectorProps: CollectorPanelProps;
  universePanel?: ReactNode;
  entriesTabs?: ReactNode;
  assetsContent?: ReactNode;
  sourcesTabs?: ReactNode;
  initialView: string;
}) {
  return (
    <CollectorPanel {...collectorProps}>
      {(collector) => (
        <>
          <WorkbenchScopeBar collector={collector} />
          <WorkbenchTabs
            label="数据管理分区"
            queryKey="data_view"
            initialKey={initialView}
            anchors={viewAnchors}
            aliases={viewAliases}
            panels={[
              {
                key: "catalog",
                title: "数据目录",
                description:
                  "按市场、板块与股票逐级圈定采集范围；股票池以冻结快照提交。",
                content: universePanel,
              },
              {
                key: "download",
                title: "多维下载",
                description:
                  "配置范围 → 预览确认 → 提交；财务、分红与逐笔兼容入口在本视图下方。",
                content: (
                  <div className={scopeStyles.padded}>
                    {collector.create}
                    <MobileSelectionBar collector={collector} />
                    {entriesTabs}
                  </div>
                ),
              },
              {
                key: "jobs",
                title: "任务与结果",
                description:
                  "执行进度 → 质量检查 → 查看数据；含数据资产与兼容任务。",
                content: (
                  <>
                    {collector.results}
                    {assetsContent}
                  </>
                ),
              },
              {
                key: "sources",
                title: "来源与新鲜度",
                description: "来源能力、用量预算、数据新鲜度与计划调度。",
                content: sourcesTabs,
              },
            ]}
          />
        </>
      )}
    </CollectorPanel>
  );
}
