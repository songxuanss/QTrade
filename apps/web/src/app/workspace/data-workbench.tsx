import type { ReactNode } from "react";
import {
  DataWorkbenchRefresh,
  WorkbenchTabs,
} from "../../features/data/workbench-tabs";
import {
  HistoryDownloadForm,
  type SourceOption,
} from "../../features/data/history-form";
import {
  DatasetQualityReport,
  type CoverageReport,
} from "../../features/data/quality-report";
import { ResearchDataPanel } from "../../features/data/research-panel";
import type { ResearchDataset } from "../../features/data/research-server";
import type {
  DataBatch,
  DataIssue,
  DataTask,
} from "../../features/auth/server";
import Link from "next/link";
import {
  claimIssueAction,
  closeIssueAction,
  createTickTaskAction,
  rerunTaskAction,
} from "./data-actions";

const date = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
const statusLabel: Record<string, string> = {
  queued: "排队中",
  running: "采集中",
  waiting: "等待重试",
  failed: "失败",
  completed: "已完成",
  partial: "部分完成",
  completed_with_gaps: "完成 · 存在缺口",
  no_data: "无观测数据",
  cancelled: "已取消",
  claimed: "处理中",
  open: "待处理",
  closed: "已关闭",
};

const failureMessages: Record<string, string> = {
  error: "操作未完成，服务暂时无法响应。请刷新后重试。",
  permission: "当前登录已失效或没有数据管理权限，请重新登录管理员账号。",
  invalid: "下载参数不符合要求，请检查股票代码和起止时间。",
  range: "下载范围超过当前配置上限，请缩短范围或调整下载上限配置。",
  archive: "当前来源仅支持北京时间 2026-08-13 起的历史逐笔，请调整开始时间。",
  unavailable: "下载服务暂不可用，请检查服务端数据源配置与服务健康状态。",
};

export function DataWorkbench({
  tasks,
  batches,
  issues,
  sources,
  canManage,
  error,
  result,
  downloadRequest,
  research,
  maxDownloadDays = null,
  sourceOptions = [],
  qualityReport = null,
  showQuality = false,
  universePanel,
  collectorPanel,
  schedulePanel,
  financialPanel,
  distributionPanel,
  healthPanel,
  usagePanel,
  sourceProfilePanel,
  compositionPanel,
  initialView = "collect",
  initialResearchView = "financial",
  initialAssetView = "research",
  initialMonitorView = "health",
  instrumentNames = {},
}: {
  universePanel?: ReactNode;
  collectorPanel?: ReactNode;
  schedulePanel?: ReactNode;
  financialPanel?: ReactNode;
  distributionPanel?: ReactNode;
  healthPanel?: ReactNode;
  usagePanel?: ReactNode;
  sourceProfilePanel?: ReactNode;
  compositionPanel?: ReactNode;
  initialView?: string;
  initialResearchView?: string;
  initialAssetView?: string;
  initialMonitorView?: string;
  instrumentNames?: Record<string, string>;
  sourceOptions?: SourceOption[];
  qualityReport?: CoverageReport | null;
  showQuality?: boolean;
  maxDownloadDays?: number | null;
  research?: { datasets: ResearchDataset[]; available: boolean };
  tasks: DataTask[];
  batches: DataBatch[];
  issues: DataIssue[];
  sources: string[];
  canManage: boolean;
  error: boolean;
  result?: string;
  downloadRequest?: {
    instrument: string;
    start: string;
    end: string;
    source?: string;
    granularity?: string;
  };
}) {
  const failed = tasks.filter((task) => task.status === "failed").length;
  const openIssues = issues.filter((issue) => issue.status !== "closed").length;
  const rejectedDownload =
    result && failureMessages[result] ? downloadRequest : undefined;
  return (
    <div className="workspace-page data-workbench">
      <div className="page-heading data-heading">
        <div>
          <p className="eyebrow">DATA OPERATIONS</p>
          <h1>数据管理</h1>
          <p className="page-description">
            按数据类别配置采集，集中追踪任务、数据资产和运行状态。
          </p>
        </div>
        <DataWorkbenchRefresh />
      </div>
      {result === "success" && (
        <p
          className={`data-notice ${downloadRequest ? "info" : "success"}`}
          role="status"
        >
          {downloadRequest
            ? "下载任务已提交，正在等待 Worker 执行；完成情况以任务进度和实际发布结果为准。"
            : "操作已完成，数据状态已更新。"}
        </p>
      )}
      {result && failureMessages[result] && (
        <p className="data-notice" role="alert">
          {failureMessages[result]}
        </p>
      )}

      <WorkbenchTabs
        label="数据管理分区"
        queryKey="data_view"
        initialKey={initialView}
        anchors={{
          "independent-collector": "collect",
          "financial-collection": "fundamentals",
          "distribution-collection": "fundamentals",
          "pool-download": "pools",
          "legacy-downloads": "ticks",
          collection: "assets",
          batches: "assets",
          quality: "assets",
          "research-data": "assets",
        }}
        panels={[
          {
            key: "collect",
            title: "行情与因子",
            content: collectorPanel,
          },
          {
            key: "pools",
            title: "板块与股票池",
            description: "按板块选择成员并冻结股票池，再批量下载所需数据。",
            content: universePanel,
          },
          {
            key: "fundamentals",
            title: "财务与事件",
            content: (
              <WorkbenchTabs
                compact
                label="研究数据类别"
                queryKey="research_view"
                initialKey={initialResearchView}
                anchors={{
                  "financial-collection": "financial",
                  "distribution-collection": "events",
                }}
                panels={[
                  {
                    key: "financial",
                    title: "财务报表",
                    content: financialPanel,
                  },
                  {
                    key: "events",
                    title: "分红、送转与配股",
                    content: distributionPanel,
                  },
                ]}
              />
            ),
          },
          {
            key: "schedules",
            title: "自动更新",
            description: "集中管理采集计划、启停状态和下一次执行时间。",
            content: schedulePanel,
          },
          {
            key: "assets",
            title: "数据资产",
            content: (
              <>
                {error && (
                  <p className="data-notice" role="alert">
                    暂时无法读取数据管理服务，请稍后刷新。
                  </p>
                )}

                <div className="data-metrics" aria-label="数据概览">
                  <article className="panel data-metric">
                    <span>兼容下载任务</span>
                    <strong>{tasks.length}</strong>
                    <small>
                      {failed ? `${failed} 项需要重跑` : "当前任务记录"}
                    </small>
                  </article>
                  <article className="panel data-metric">
                    <span>原始批次</span>
                    <strong>{batches.length}</strong>
                    <small>已登记的数据批次</small>
                  </article>
                  <article className="panel data-metric">
                    <span>未关闭质量问题</span>
                    <strong>{openIssues}</strong>
                    <small>共 {issues.length} 项质量记录</small>
                  </article>
                </div>

                <WorkbenchTabs
                  compact
                  label="数据资产类别"
                  queryKey="asset_view"
                  initialKey={initialAssetView}
                  anchors={{
                    collection: "tasks",
                    batches: "raw",
                    quality: "quality",
                    "research-data": "research",
                  }}
                  panels={[
                    {
                      key: "research",
                      title: "研究数据集",
                      content: (
                        <>
                          {" "}
                          {research && (
                            <ResearchDataPanel
                              tasks={tasks}
                              datasets={research.datasets}
                              available={research.available}
                              canManage={canManage}
                              result={result}
                            />
                          )}
                          {compositionPanel}
                        </>
                      ),
                    },
                    {
                      key: "raw",
                      title: "原始批次",
                      content: (
                        <>
                          {" "}
                          <section id="batches" className="panel data-section">
                            <div className="data-section-title">
                              <div>
                                <p className="eyebrow">RAW BATCHES</p>
                                <h2>原始批次</h2>
                              </div>
                              <span>{batches.length} 项</span>
                            </div>
                            {batches.length ? (
                              <div
                                className="data-list"
                                role="region"
                                aria-label="数据记录列表"
                                tabIndex={0}
                              >
                                {batches.map((batch) => (
                                  <article
                                    className="data-row"
                                    key={batch.batch_id}
                                  >
                                    <div className="data-row-main">
                                      <strong>
                                        {instrumentNames[batch.instrument_id] ??
                                          "名称未核验"}{" "}
                                        · {batch.instrument_id}
                                        <small>
                                          {batch.source} · {batch.granularity}
                                        </small>
                                      </strong>
                                      <span className="data-secondary">
                                        {batch.record_count.toLocaleString()}{" "}
                                        条记录
                                      </span>
                                      <span className="data-secondary">
                                        {date(batch.collected_at)}
                                      </span>
                                      <code
                                        className="data-hash"
                                        title={batch.content_sha256}
                                      >
                                        SHA-256{" "}
                                        {batch.content_sha256.slice(0, 16)}…
                                      </code>
                                    </div>
                                  </article>
                                ))}
                              </div>
                            ) : (
                              <p className="data-empty">
                                尚无已登记批次。批次在采集 Worker
                                持久化原始数据后出现在这里。
                              </p>
                            )}
                          </section>
                        </>
                      ),
                    },
                    {
                      key: "quality",
                      title: "质量问题",
                      content: (
                        <>
                          {" "}
                          {showQuality && (
                            <DatasetQualityReport
                              report={qualityReport}
                              unavailable={!qualityReport}
                            />
                          )}
                          <section id="quality" className="panel data-section">
                            <div className="data-section-title">
                              <div>
                                <p className="eyebrow">DATA QUALITY</p>
                                <h2>质量问题</h2>
                              </div>
                              <span>{issues.length} 项</span>
                            </div>
                            {issues.length ? (
                              <div
                                className="data-list"
                                role="region"
                                aria-label="数据记录列表"
                                tabIndex={0}
                              >
                                {issues.map((issue) => (
                                  <article
                                    className="data-row issue-row"
                                    key={issue.issue_id}
                                  >
                                    <div className="data-row-main">
                                      <span
                                        className={`data-status status-${issue.status}`}
                                      >
                                        {statusLabel[issue.status] ??
                                          issue.status}
                                      </span>
                                      <strong>
                                        {instrumentNames[issue.instrument_id] ??
                                          "名称未核验"}{" "}
                                        · {issue.instrument_id}
                                        <small>
                                          {issue.code} · {issue.severity} ·{" "}
                                          {issue.granularity}
                                        </small>
                                      </strong>
                                      <span className="data-period">
                                        {date(issue.starts_at)} —{" "}
                                        {date(issue.ends_at)}
                                      </span>
                                      <span className="data-secondary">
                                        发现于 {date(issue.detected_at)}
                                      </span>
                                      <details>
                                        <summary>查看问题证据</summary>
                                        <pre>
                                          {JSON.stringify(
                                            issue.evidence,
                                            null,
                                            2,
                                          )}
                                        </pre>
                                      </details>
                                    </div>
                                    {canManage && issue.status !== "closed" && (
                                      <div className="data-actions">
                                        {issue.status === "open" && (
                                          <form action={claimIssueAction}>
                                            <input
                                              type="hidden"
                                              name="issue_id"
                                              value={issue.issue_id}
                                            />
                                            <button
                                              className="button button-secondary"
                                              type="submit"
                                            >
                                              认领
                                            </button>
                                          </form>
                                        )}
                                        <form action={closeIssueAction}>
                                          <input
                                            type="hidden"
                                            name="issue_id"
                                            value={issue.issue_id}
                                          />
                                          <button
                                            className="button button-quiet"
                                            type="submit"
                                          >
                                            关闭问题
                                          </button>
                                        </form>
                                      </div>
                                    )}
                                  </article>
                                ))}
                              </div>
                            ) : (
                              <p className="data-empty">
                                暂无质量问题。扫描结果会按工作区归档并保留证据。
                              </p>
                            )}
                          </section>
                        </>
                      ),
                    },
                    {
                      key: "tasks",
                      title: "兼容采集任务",
                      content: (
                        <>
                          {" "}
                          <section
                            id="collection"
                            className="panel data-section"
                          >
                            <p className="data-secondary">
                              新建下载超过 7
                              天时自动拆分为多个子任务，各段独立执行和重试。
                            </p>
                            <div className="data-section-title">
                              <div>
                                <p className="eyebrow">COLLECTION</p>
                                <h2>采集任务</h2>
                              </div>
                              <span>{tasks.length} 项</span>
                            </div>
                            {tasks.length ? (
                              <div
                                className="data-list"
                                role="region"
                                aria-label="数据记录列表"
                                tabIndex={0}
                              >
                                {tasks.map((task) => (
                                  <article
                                    className="data-row"
                                    key={task.task_id}
                                    data-task-id={task.task_id}
                                  >
                                    <div className="data-row-main">
                                      <span
                                        className={`data-status status-${task.status}`}
                                      >
                                        {statusLabel[task.status] ??
                                          task.status}
                                      </span>
                                      <strong>
                                        {instrumentNames[task.instrument_id] ??
                                          "名称未核验"}{" "}
                                        · {task.instrument_id}
                                        <small>
                                          {task.source} · {task.granularity}
                                          {(task.part_count ?? 1) > 1 && (
                                            <>
                                              {" "}
                                              · 自动拆分 {task.part_index}/
                                              {task.part_count}
                                              （每段最多 7 天）
                                            </>
                                          )}
                                        </small>
                                      </strong>
                                      <span className="data-period">
                                        {date(task.start)} — {date(task.end)}
                                      </span>
                                      <span className="data-secondary">
                                        {task.page_count} 批次 · {task.failures}{" "}
                                        次失败
                                        {task.last_error_code
                                          ? ` · ${task.last_error_code}`
                                          : ""}
                                      </span>
                                    </div>
                                    {canManage && task.status === "failed" && (
                                      <form action={rerunTaskAction}>
                                        <input
                                          type="hidden"
                                          name="task_id"
                                          value={task.task_id}
                                        />
                                        <button
                                          className="button button-secondary"
                                          type="submit"
                                        >
                                          重新排队
                                        </button>
                                      </form>
                                    )}
                                  </article>
                                ))}
                              </div>
                            ) : (
                              <p className="data-empty">
                                尚无采集任务。配置数据源和 Worker
                                后，任务将显示在这里。
                              </p>
                            )}
                          </section>
                        </>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
          {
            key: "monitor",
            title: "运行监控",
            content: (
              <WorkbenchTabs
                compact
                label="采集运行监控"
                queryKey="monitor_view"
                initialKey={initialMonitorView}
                panels={[
                  { key: "health", title: "数据新鲜度", content: healthPanel },
                  { key: "usage", title: "用量与配额", content: usagePanel },
                  {
                    key: "sources",
                    title: "来源能力",
                    content: sourceProfilePanel,
                  },
                ]}
              />
            ),
          },
          {
            key: "ticks",
            title: "逐笔与兼容",
            description:
              "逐笔原始归档和既有下载入口；日线、分钟与因子请使用“行情与因子”。",
            content: (
              <>
                {" "}
                {error && (
                  <p className="data-notice" role="alert">
                    暂时无法读取数据管理服务，请稍后刷新。
                  </p>
                )}
                {canManage &&
                  downloadRequest?.instrument &&
                  (result === "complete" || result === "partial") && (
                    <section
                      className="panel data-section"
                      aria-labelledby="download-choice-title"
                    >
                      <h2 id="download-choice-title">
                        {result === "complete"
                          ? "该时段已完整下载"
                          : "该时段已有下载记录"}
                      </h2>
                      <p className="page-description">
                        {downloadRequest.instrument} · {downloadRequest.start} —{" "}
                        {downloadRequest.end} UTC。
                        {result === "complete"
                          ? "无需重复下载。只有需要重新获取来源数据时才选择重新下载。"
                          : "请选择重新下载全部，或仅补齐缺失数据。补采会保留已完成范围，续接可恢复任务的检查点；正在下载的任务会继续处理。"}
                      </p>
                      <form
                        action={createTickTaskAction}
                        className="data-actions"
                      >
                        <input
                          type="hidden"
                          name="source"
                          value={
                            downloadRequest.source ?? "mairui.a-share.tick"
                          }
                        />
                        <input
                          type="hidden"
                          name="granularity"
                          value={downloadRequest.granularity ?? "tick"}
                        />
                        <input
                          type="hidden"
                          name="authorization_id"
                          value="mairui-default"
                        />
                        <input
                          type="hidden"
                          name="instrument_id"
                          value={downloadRequest.instrument}
                        />
                        <input
                          type="hidden"
                          name="start"
                          value={downloadRequest.start}
                        />
                        <input
                          type="hidden"
                          name="end"
                          value={downloadRequest.end}
                        />
                        <button
                          className="button button-secondary"
                          name="mode"
                          value="all"
                          type="submit"
                        >
                          重新下载全部
                        </button>
                        {result === "partial" && (
                          <button
                            className="button button-primary"
                            name="mode"
                            value="missing"
                            type="submit"
                          >
                            仅下载缺失数据
                          </button>
                        )}
                        <Link
                          className="button button-secondary"
                          href="/workspace/data"
                        >
                          取消
                        </Link>
                      </form>
                    </section>
                  )}
                <section
                  id="legacy-downloads"
                  className="data-legacy-group"
                  aria-labelledby="legacy-downloads-title"
                >
                  <div className="data-legacy-heading">
                    <div>
                      <p className="eyebrow">TICK & COMPATIBILITY</p>
                      <h2 id="legacy-downloads-title">逐笔与兼容历史下载</h2>
                      <p>
                        用于原始逐笔归档及既有单标的下载入口；分钟与因子采集请切换“行情与因子”。
                      </p>
                    </div>
                  </div>
                  <section className="data-source panel">
                    <span className="source-indicator" aria-hidden="true" />
                    <div>
                      <strong>
                        {sources.includes("mairui.a-share.tick")
                          ? "A 股历史逐笔来源已就绪"
                          : "A 股历史逐笔来源待配置"}
                      </strong>
                      <p>
                        {sources.includes("mairui.a-share.tick")
                          ? "MaiRui 来源已配置。提交后由采集 Worker 处理，请通过任务进度确认实际下载结果。"
                          : "配置服务端 MAIRUI_LICENCE 并启动 Worker 后，可提交指定标的和时间范围。"}
                      </p>
                    </div>
                    <span className="data-chip">
                      {sources.includes("mairui.a-share.tick")
                        ? "可采集"
                        : "等待配置"}
                    </span>
                  </section>
                  {canManage && (
                    <section
                      className="panel data-section"
                      aria-labelledby="tick-download-title"
                    >
                      <div className="data-section-title">
                        <div>
                          <p className="eyebrow">HISTORICAL TICKS</p>
                          <h2 id="tick-download-title">下载指定时段</h2>
                        </div>
                        <span>
                          {error
                            ? "范围配置暂不可用"
                            : maxDownloadDays === null
                              ? "下载时长不限"
                              : `最多 ${maxDownloadDays} 天`}
                        </span>
                      </div>
                      <p className="page-description">
                        选择 A 股代码并填写 UTC
                        起止时间。采集结果以原始批次保存，可按页恢复；当前接入源的归档范围从
                        2026-08-13 起。港股历史逐笔源暂未接通。
                      </p>
                      <form
                        key={
                          rejectedDownload
                            ? `${rejectedDownload.instrument}|${rejectedDownload.start}|${rejectedDownload.end}`
                            : "new"
                        }
                        action={createTickTaskAction}
                        className="tick-request-form"
                      >
                        <input
                          type="hidden"
                          name="source"
                          value="mairui.a-share.tick"
                        />
                        <input
                          type="hidden"
                          name="authorization_id"
                          value="mairui-default"
                        />
                        <label>
                          A 股代码（6 位）
                          <input
                            name="instrument_id"
                            required
                            maxLength={256}
                            pattern="(?:CN\.)?\d{6}(?:\.(?:SH|SZ|BJ))?"
                            placeholder="例如 600519.SH"
                            defaultValue={rejectedDownload?.instrument}
                          />
                        </label>
                        <label>
                          开始时间（UTC）
                          <input
                            name="start"
                            type="datetime-local"
                            required
                            defaultValue={rejectedDownload?.start}
                          />
                        </label>
                        <label>
                          结束时间（UTC）
                          <input
                            name="end"
                            type="datetime-local"
                            required
                            defaultValue={rejectedDownload?.end}
                          />
                        </label>
                        <button
                          className="button button-primary"
                          type="submit"
                          disabled={
                            error || !sources.includes("mairui.a-share.tick")
                          }
                        >
                          创建 Tick 下载任务
                        </button>
                      </form>
                    </section>
                  )}
                  {canManage && (
                    <details className="legacy-history-entry">
                      <summary>旧版日线与分钟下载入口</summary>
                      <p className="data-footnote">
                        仅用于维护旧版任务与兼容流程。新采集请使用“行情与因子”分区；两处任务的进度和发布结果分别展示。
                      </p>
                      <HistoryDownloadForm
                        options={sourceOptions}
                        available={!error}
                      />
                    </details>
                  )}
                </section>
              </>
            ),
          },
        ]}
      />
      {!canManage && (
        <p className="data-footnote">
          当前为只读访问。重新排队、认领和关闭问题仅对工作区管理员开放。
        </p>
      )}
    </div>
  );
}
