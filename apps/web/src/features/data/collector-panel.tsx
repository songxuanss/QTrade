"use client";

import { CollectionReplayControl } from "./collection-replay";
import Link from "next/link";
import workspaceStyles from "./collector-workspace.module.css";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  controlCollector,
  previewCollector,
  readCollectorJob,
  readCollectorDefaultRange,
  readCollectorRecords,
  submitCollector,
  saveCollectorPreferences,
} from "../../app/workspace/collector-actions";
import { collectionDates } from "./collection-dates";
import { FactorFieldPicker } from "./factor-field-picker";
import { FactorCatalogStatus } from "./factor-catalog-status";
import { FreshnessPanel } from "./freshness-panel";
import { CollectionQualityPanel } from "./collection-quality-panel";
import type {
  CollectorJob,
  CollectorDefaultRange,
  CollectorPreview,
  CollectorPublication,
  CollectorRecord,
  CollectorSource,
  FactorDefinition,
  FactorCatalogSnapshot,
  CollectorPreferences,
} from "./collector-types";
import type { UniverseSnapshot } from "./universe-server";

function subscribeView(onChange: () => void) {
  window.addEventListener("popstate", onChange);
  window.addEventListener("collector-navigation", onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener("collector-navigation", onChange);
  };
}

const states: Record<string, string> = {
  queued: "排队中",
  running: "采集中",
  waiting: "等待重试",
  failed: "失败",
  completed: "采集完成",
  completed_with_gaps: "完成 · 存在无数据分段",
  no_data: "无观测数据",
  cancelled: "已取消",
};
const errors: Record<string, string> = {
  unavailable: "采集服务暂不可用，请检查独立服务或授权连接。",
  unauthorized: "来源权限验证未通过，请检查订阅和采集配置。",
  forbidden: "当前账号没有该作用域的操作权限。",
  unsupported: "该来源不支持所选市场、资产类别、周期或字段。",
  invalid_input: "请检查标的、日期范围和字段选择。",
  incomplete_dates:
    "请同时填写开始和结束日期；两个日期都留空时才采用来源默认范围。",
  default_unavailable:
    "该类别或周期的默认发布时间尚未验证，请明确填写日期后预览。",
  conflict: "计划覆盖已变化，请重新预览后提交。",
  unverified: "来源数据未通过结构或单位校验，未发布为可见行情。",
  rate_limited: "来源限流，任务会按退避计划重试。",
  timeout: "来源请求超时，任务会按退避计划重试。",
};
const periods: Record<string, string> = {
  "1d": "日线",
  "1m": "1 分钟",
  "5m": "5 分钟",
  "15m": "15 分钟",
  "30m": "30 分钟",
  "1h": "60 分钟",
};
const budgetWaits = {
  source_daily: "来源日信用不足，等待预算重置；管理员调整额度后可提前重试。",
  scope_daily: "当前空间日信用不足，等待预算重置；管理员调整额度后可提前重试。",
  source_concurrency: "来源并发预留已满，等待释放后再尝试。",
  scope_concurrency: "当前空间并发预留已满，等待释放后再尝试。",
};

export function CollectorPanel({
  catalog,
  factors,
  factorsAvailable,
  factorCatalog = null,
  initialJobs,
  available,
  canManage,
  snapshots,
  initialPreferences = null,
  preferencesAvailable = true,
}: {
  catalog: CollectorSource[];
  factors: FactorDefinition[];
  factorsAvailable: boolean;
  factorCatalog?: FactorCatalogSnapshot | null;
  initialJobs: CollectorJob[];
  available: boolean;
  canManage: boolean;
  snapshots: UniverseSnapshot[];
  initialPreferences?: CollectorPreferences | null;
  preferencesAvailable?: boolean;
}) {
  const [catalogState, setCatalogState] = useState(factorCatalog);
  const displayedFactors = catalogState?.definitions ?? factors;
  const factorSelectionAvailable = catalogState
    ? catalogState.definitions.length > 0
    : factorsAvailable;
  const [kind, setKind] = useState<"bars" | "factors">(
    initialPreferences?.dataset_kind ?? "bars",
  );
  const [sourceId, setSourceId] = useState(
    (initialPreferences?.source ||
      catalog.find((item) => item.configured && item.periods.includes("1d"))
        ?.source) ??
      catalog[0]?.source ??
      "",
  );
  const sources = catalog.filter((item) => item.dataset_kind === kind);
  const source = sourceId
    ? sources.find((item) => item.source === sourceId)
    : sources[0];
  const [period, setPeriod] = useState(initialPreferences?.granularity ?? "1d");
  const [mode, setMode] = useState<"missing" | "all">(
    initialPreferences?.mode ?? "missing",
  );
  const [first, setFirst] = useState(initialPreferences?.first ?? "");
  const [last, setLast] = useState(initialPreferences?.last ?? "");
  const [instruments, setInstruments] = useState(
    initialPreferences?.instruments ?? "",
  );
  const [snapshotIds, setSnapshotIds] = useState<string[]>(
    initialPreferences?.snapshot_ids?.length
      ? initialPreferences.snapshot_ids
      : initialPreferences?.snapshot_id
        ? [initialPreferences.snapshot_id]
        : [],
  );
  const [savedSelection, setSavedSelection] = useState(false);
  const [defaultRange, setDefaultRange] =
    useState<CollectorDefaultRange | null>(null);
  const selectionRevision = useRef(0);
  const [preview, setPreview] = useState<CollectorPreview | null>(null);
  const [planConfirmed, setPlanConfirmed] = useState(false);
  const [job, setJob] = useState<CollectorJob | null>(null);
  const [jobs, setJobs] = useState(initialJobs);
  const [jobReadError, setJobReadError] = useState("");
  const [lastJobUpdate, setLastJobUpdate] = useState<number | null>(null);
  // Manual selection advances this generation so an older read cannot replace
  // the task the user chose later.
  const jobRequestRevision = useRef(0);
  const view = useSyncExternalStore(
    subscribeView,
    () => {
      const query = new URL(location.href).searchParams;
      return !canManage ||
        query.get("collect_view") === "results" ||
        (query.has("collector_job") && query.get("collect_view") !== "create")
        ? "results"
        : "create";
    },
    () => (canManage ? "create" : "results"),
  );

  // Navigation owns only presentation. Both views remain mounted so moving
  // between a running task and its configuration never discards draft inputs.
  function navigate(next: "create" | "results") {
    const url = new URL(location.href);
    url.searchParams.set("collect_view", next);
    history.replaceState(null, "", url);
    window.dispatchEvent(new Event("collector-navigation"));
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selectedFields, setSelectedFields] = useState<string[]>(
    initialPreferences?.fields ?? ["pe_ttm"],
  );
  const [publication, setPublication] = useState<CollectorPublication | null>(
    null,
  );
  const [records, setRecords] = useState<CollectorRecord[]>([]);
  const [nextRecordOffset, setNextRecordOffset] = useState<number | null>(null);

  const applyJob = useCallback((next: CollectorJob) => {
    setJob(next);
    setJobs((current) => {
      const exists = current.some((item) => item.job_id === next.job_id);
      return (
        exists
          ? current.map((item) => (item.job_id === next.job_id ? next : item))
          : [next, ...current]
      ).slice(0, 20);
    });
    setLastJobUpdate(Date.now());
    setJobReadError("");
  }, []);
  useEffect(() => {
    const url = new URL(location.href);
    const id = url.searchParams.get("collector_job");
    if (!id) return;
    const request = ++jobRequestRevision.current;
    let cancelled = false;
    void readCollectorJob(id)
      .then((result) => {
        if (cancelled || jobRequestRevision.current !== request) return;
        if (result.job) applyJob(result.job);
        else setJobReadError(result.error ?? "unavailable");
      })
      .catch(() => {
        if (!cancelled && jobRequestRevision.current === request)
          setJobReadError("unavailable");
      });
    return () => {
      cancelled = true;
      jobRequestRevision.current += 1;
    };
  }, [applyJob]);
  const pending = Boolean(
    job &&
    (["queued", "running", "waiting"].includes(job.state) ||
      job.parts.some(
        (part) =>
          part.publication?.dataset_kind === "bars" &&
          part.consumer?.state !== "visible",
      )),
  );
  const activeJobId = job?.job_id;
  const activeJobOffset = job?.offset;
  useEffect(() => {
    if (!activeJobId || activeJobOffset === undefined || !pending) return;
    const id = activeJobId;
    const offset = activeJobOffset;
    let cancelled = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const request = jobRequestRevision.current;
      try {
        const result = await readCollectorJob(id, offset);
        if (cancelled || jobRequestRevision.current !== request) return;
        if (result.job) {
          applyJob(result.job);
          failures = 0;
        } else {
          failures += 1;
          setJobReadError(result.error ?? "unavailable");
        }
      } catch {
        if (!cancelled && jobRequestRevision.current === request) {
          failures += 1;
          setJobReadError("unavailable");
        }
      } finally {
        if (!cancelled) {
          // Keep an outage from creating a tight request loop while still
          // surfacing queued-to-running transitions promptly after recovery.
          const delay = Math.min(30_000, 3_000 * 2 ** Math.min(failures, 4));
          timer = setTimeout(() => void poll(), delay);
        }
      }
    };
    timer = setTimeout(() => void poll(), 3000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [activeJobId, activeJobOffset, applyJob, pending]);

  function invalidateSelection() {
    selectionRevision.current += 1;
    setSavedSelection(false);
    setDefaultRange(null);
    setPreview(null);
  }

  function choose(job: CollectorJob) {
    jobRequestRevision.current += 1;
    applyJob(job);
    setRecords([]);
    setPublication(null);
    const url = new URL(location.href);
    url.searchParams.set("collector_job", job.job_id);
    url.searchParams.set("collect_view", "results");
    history.replaceState(null, "", url);
    window.dispatchEvent(new Event("collector-navigation"));
  }
  async function fillDefaultDates() {
    if (!source || first || last) return;
    const revision = selectionRevision.current;
    setBusy(true);
    setError("");
    try {
      const result = await readCollectorDefaultRange(source.source, period);
      if (selectionRevision.current !== revision) return;
      if (result.range) {
        setFirst(result.range.first);
        setLast(result.range.last);
        setDefaultRange(result.range);
        setPreview(null);
      } else setError(result.error ?? "unavailable");
    } finally {
      setBusy(false);
    }
  }
  async function saveSelection() {
    const revision = selectionRevision.current;
    setBusy(true);
    setError("");
    setSavedSelection(false);
    try {
      const result = await saveCollectorPreferences({
        schema_version: 1,
        dataset_kind: kind,
        source: sourceId || source?.source || "",
        granularity: period,
        first: first || null,
        last: last || null,
        instruments,
        snapshot_id: snapshotIds[0] ?? null,
        snapshot_ids: snapshotIds,
        fields: selectedFields,
        mode,
      });
      if (result.error) setError(result.error);
      else if (selectionRevision.current === revision) setSavedSelection(true);
    } finally {
      setBusy(false);
    }
  }
  async function build(form: FormData) {
    const revision = selectionRevision.current;
    setBusy(true);
    setPreview(null);
    setError("");
    try {
      if (!source) throw Error("unavailable");
      const ids = [
        ...new Set(
          String(form.get("instruments") ?? "")
            .split(/[\s,，;；]+/)
            .filter(Boolean),
        ),
      ];
      if (!ids.length && !snapshotIds.length) throw Error("invalid_input");
      if (kind === "bars" && !source.periods.includes(period))
        throw Error("unsupported");
      let firstDate = String(form.get("first") ?? "");
      let lastDate = String(form.get("last") ?? "");
      if (Boolean(firstDate) !== Boolean(lastDate))
        throw Error("incomplete_dates");
      if (!firstDate && !lastDate) {
        const result = await readCollectorDefaultRange(
          source.source,
          kind === "factors" ? "1d" : period,
        );
        if (selectionRevision.current !== revision) return;
        if (!result.range) {
          setError(
            result.error === "unsupported"
              ? "default_unavailable"
              : (result.error ?? "unavailable"),
          );
          return;
        }
        firstDate = result.range.first;
        lastDate = result.range.last;
        setFirst(firstDate);
        setLast(lastDate);
        setDefaultRange(result.range);
      }
      const dates = collectionDates(firstDate, lastDate, source.timezone);
      const response = await previewCollector({
        source: source.source,
        dataset_kind: kind,
        instrument_ids: ids,
        snapshot_ids: snapshotIds,
        granularity: kind === "factors" ? "1d" : period,
        ...dates,
        fields: kind === "factors" ? selectedFields : [],
        mode,
      });
      if (selectionRevision.current !== revision) return;
      if (response.preview) {
        setPreview(response.preview);
        setPlanConfirmed(false);
      } else setError(response.error ?? "unavailable");
    } catch (failure) {
      setError(
        failure instanceof Error &&
          ["unsupported", "incomplete_dates", "unavailable"].includes(
            failure.message,
          )
          ? failure.message
          : "invalid_input",
      );
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      const response = await submitCollector(preview, planConfirmed);
      if (response.job) {
        choose(response.job);
        setPreview(null);
      } else setError(response.error ?? "unavailable");
    } finally {
      setBusy(false);
    }
  }
  async function refresh(id: string, offset = 0) {
    const request = ++jobRequestRevision.current;
    try {
      const result = await readCollectorJob(id, offset);
      if (jobRequestRevision.current !== request) return;
      if (result.job) choose(result.job);
      else setJobReadError(result.error ?? "unavailable");
    } catch {
      if (jobRequestRevision.current === request)
        setJobReadError("unavailable");
    }
  }
  async function control(action: "retry" | "cancel") {
    if (!job) return;
    setBusy(true);
    const result = await controlCollector(job.job_id, action);
    if (result.error) setError(result.error);
    else await refresh(job.job_id);
    setBusy(false);
  }
  async function browse(value: CollectorPublication, offset = 0) {
    const result = await readCollectorRecords(value.publication_id, offset);
    if (result.records) {
      setPublication(value);
      setRecords(result.records);
      setNextRecordOffset(result.next_offset ?? null);
    } else setError(result.error ?? "unavailable");
  }

  const selectedPools = snapshots.filter((item) =>
    snapshotIds.includes(item.snapshot_id),
  );
  const unresolvedPools = snapshotIds.filter(
    (id) => !snapshots.some((item) => item.snapshot_id === id),
  );
  const manualIds = [
    ...new Set(
      instruments
        .split(/[\s,，;；]+/)
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  ];
  const combinedIds = new Set([
    ...manualIds,
    ...selectedPools.flatMap((item) =>
      item.members.map((member) => member.instrument.instrument_id),
    ),
  ]);

  return (
    <section
      className={`panel collector-workbench ${workspaceStyles.workbench}`}
      id="independent-collector"
      aria-labelledby="collector-title"
    >
      <div className={workspaceStyles.heading}>
        <div>
          <h2 id="collector-title">独立数据采集</h2>
        </div>
        <span
          className={`data-chip collector-connection ${available ? "is-connected" : "is-disconnected"}`}
        >
          <span aria-hidden="true" />
          {available ? "远程服务已连接" : "服务待连接"}
        </span>
      </div>
      {!preferencesAvailable && (
        <p className="data-footnote" role="status">
          已保存选择暂不可读取，当前显示默认输入。请刷新重试或检查业务
          API；保存新选择会覆盖原草稿。
        </p>
      )}
      {!available && (
        <p className="data-notice" role="alert">
          独立采集服务尚未连接。旧任务仍在下方保留。
        </p>
      )}
      {error && (
        <p className="data-notice" role="alert">
          {errors[error] ?? "操作暂未完成，请刷新重试。"}
        </p>
      )}
      <div
        className={workspaceStyles.toolbar}
        role="group"
        aria-label="采集工作视图"
      >
        <div className={workspaceStyles.switcher}>
          {canManage && (
            <button
              type="button"
              aria-pressed={view === "create"}
              onClick={() => navigate("create")}
            >
              新建采集
            </button>
          )}
          <button
            type="button"
            aria-pressed={view === "results"}
            onClick={() => navigate("results")}
          >
            任务与结果
          </button>
        </div>
        <span className={workspaceStyles.hint}>
          {view === "create"
            ? "配置范围 → 预览确认 → 提交"
            : "执行进度 → 质量检查 → 查看数据"}
        </span>
      </div>
      <div className={workspaceStyles.view} hidden={view !== "create"}>
        <div className={`collector-layout ${workspaceStyles.createLayout}`}>
          <div className="collector-builder">
            {canManage && available && (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void build(new FormData(event.currentTarget));
                }}
                onChange={invalidateSelection}
                className={`collector-form ${workspaceStyles.form}`}
              >
                <div className="collector-wide collector-zone-heading">
                  <div>
                    <span className="collector-step-number">01</span>
                    <div>
                      <h3>配置采集范围</h3>
                      <p>先确定类别、标的与日期，再预览计划。</p>
                    </div>
                  </div>
                </div>
                <div className="collector-form-section-title">
                  <span>数据类型与来源</span>
                </div>
                <label className="collector-kind-field">
                  数据类别
                  <select
                    aria-label="采集数据类别"
                    value={kind}
                    onChange={(e) => {
                      setKind(e.target.value as "bars" | "factors");
                      setSourceId("");
                    }}
                  >
                    <option value="bars">价格行情 · 日线与分钟</option>
                    <option value="factors">量化因子 · 历史序列</option>
                  </select>
                </label>
                <label className="collector-source-field">
                  数据来源
                  <select
                    aria-label="采集数据来源"
                    value={sourceId || source?.source || ""}
                    onChange={(e) => setSourceId(e.target.value)}
                  >
                    {sourceId &&
                      !sources.some((item) => item.source === sourceId) && (
                        <option value={sourceId}>
                          {sourceId} · 已保存来源不可用，请重新选择
                        </option>
                      )}
                    {sources.map((item) => (
                      <option
                        key={item.source}
                        value={item.source}
                        disabled={!item.configured}
                      >
                        {item.name}
                        {!item.configured ? " · 未配置" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="collector-period-field">
                  周期
                  <select
                    aria-label="独立采集周期"
                    value={kind === "factors" ? "1d" : period}
                    onChange={(e) => setPeriod(e.target.value)}
                  >
                    {kind === "bars" &&
                      (!source || !source.periods.includes(period)) && (
                        <option value={period}>
                          {periods[period]} · 当前来源不支持
                        </option>
                      )}
                    {source?.periods.map((value) => (
                      <option key={value} value={value}>
                        {periods[value] ?? value}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="collector-form-section-title">
                  <span>标的范围</span>
                </div>
                <fieldset
                  className="collector-wide collector-pool-picker"
                  aria-label="独立采集股票池"
                >
                  <legend>行业、主题与指数股票池（可多选）</legend>
                  <p>
                    可同时选择多个行业/主题/指数池，也可在下方追加公司代码；成员合并去重后提交，提交时冻结当前成员快照。
                  </p>
                  <div className="collector-pool-options">
                    {snapshots.map((item) => (
                      <label key={item.snapshot_id}>
                        <input
                          type="checkbox"
                          value={item.snapshot_id}
                          checked={snapshotIds.includes(item.snapshot_id)}
                          onChange={(event) => {
                            invalidateSelection();
                            setSnapshotIds((current) =>
                              event.target.checked
                                ? [...new Set([...current, item.snapshot_id])]
                                : current.filter(
                                    (id) => id !== item.snapshot_id,
                                  ),
                            );
                          }}
                        />
                        <span>
                          {item.name}
                          <small>{item.members.length} 个成员</small>
                        </span>
                      </label>
                    ))}
                    {snapshots.length === 0 && <span>暂无已保存股票池</span>}
                  </div>
                  {unresolvedPools.length > 0 && (
                    <p role="alert">
                      已选 {unresolvedPools.length}{" "}
                      个股票池不在当前列表，将保留选择但请确认可用性。
                    </p>
                  )}
                </fieldset>
                <label className="collector-wide">
                  追加公司代码（可与上方股票池组合）
                  <input
                    name="instruments"
                    value={instruments}
                    onChange={(event) => setInstruments(event.target.value)}
                    aria-label="独立采集标的"
                    placeholder="601288.SH, 600519.SH；指数使用 index:000001.SH"
                    maxLength={20000}
                  />
                </label>
                <p
                  className={`collector-wide collector-scope-note ${combinedIds.size ? "" : "is-empty"}`}
                >
                  {combinedIds.size
                    ? `本次合并范围：${selectedPools.length} 个股票池、${manualIds.length} 个手工标的，去重后 ${combinedIds.size} 个标的。提交时冻结所选池成员。`
                    : "尚未选择范围：勾选至少一个行业/指数池，或填写公司代码后才能预览。"}
                </p>
                <div className="collector-form-section-title">
                  <span>时间范围与下载策略</span>
                </div>
                <label className="collector-date-field">
                  开始日期（{source?.timezone ?? "UTC"}）
                  <input
                    name="first"
                    aria-label="独立采集开始日期"
                    type="date"
                    value={first}
                    onChange={(event) => setFirst(event.target.value)}
                  />
                </label>
                <label className="collector-date-field">
                  结束日期（含当日）
                  <input
                    name="last"
                    aria-label="独立采集结束日期"
                    type="date"
                    value={last}
                    onChange={(event) => setLast(event.target.value)}
                  />
                </label>
                <label className="collector-mode-field">
                  下载方式
                  <select
                    aria-label="独立采集下载方式"
                    value={mode}
                    onChange={(e) =>
                      setMode(e.target.value as "missing" | "all")
                    }
                  >
                    <option value="missing">仅下载缺失数据</option>
                    <option value="all">重新下载全部数据</option>
                  </select>
                </label>
                {kind === "bars" &&
                  source?.source === "mairui.history.bars" &&
                  period === "1d" && (
                    <div
                      className={`collector-wide ${workspaceStyles.dateShortcut}`}
                    >
                      <button
                        type="button"
                        className="button button-secondary"
                        disabled={
                          busy || Boolean(first || last) || !source.configured
                        }
                        onClick={() => void fillDefaultDates()}
                      >
                        使用最近自然年日期
                      </button>
                      <details className={workspaceStyles.help}>
                        <summary>日期计算说明</summary>
                        <p>
                          仅在两个日期都未填写时应用，保留自定义日期。按来源交易日历和预计
                          17:10 日线发布计算，不代表来源已实际更新。
                        </p>
                      </details>
                      {defaultRange && (
                        <p className="data-footnote" role="status">
                          范围 [{defaultRange.start}, {defaultRange.end}
                          )；市场时区 {defaultRange.timezone}
                          ，开始包含、结束不包含。依据：{defaultRange.basis}
                        </p>
                      )}
                    </div>
                  )}
                {kind === "factors" && (
                  <fieldset className="collector-wide collector-fields">
                    <legend>选择历史因子</legend>
                    <FactorCatalogStatus
                      snapshot={catalogState}
                      onUpdated={(snapshot) => {
                        if (
                          catalogState?.content_sha256 !==
                          snapshot.content_sha256
                        )
                          invalidateSelection();
                        setCatalogState(snapshot);
                      }}
                    />
                    {!factorSelectionAvailable && (
                      <p role="alert">
                        因子目录读取失败，当前无法创建因子任务。
                      </p>
                    )}
                    <FactorFieldPicker
                      definitions={displayedFactors}
                      selected={selectedFields}
                      onChange={(fields) => {
                        // Removing a chip emits no native change event. Invalidate
                        // the frozen plan here too, before accepting new fields.
                        invalidateSelection();
                        setSelectedFields(fields);
                      }}
                    />
                  </fieldset>
                )}
                <div className="collector-wide collector-footer">
                  {/* Disclosure retains source caveats without placing long
                      reference text ahead of the primary workflow. */}
                  <details className={workspaceStyles.help}>
                    <summary>来源能力与采集说明</summary>
                    <p>
                      {source?.limitations.join(" ")}{" "}
                      较长范围会自动拆分为可恢复的分段任务。
                      手工标的优先于股票池；留空时使用冻结池成员。当前接入 A
                      股与指数。
                      {kind === "factors" && "因子历史可知时间尚未验证。"}
                    </p>
                  </details>
                  <button
                    type="submit"
                    className="button button-primary"
                    disabled={
                      busy ||
                      !source?.configured ||
                      (kind === "factors" &&
                        (!factorSelectionAvailable ||
                          selectedFields.length === 0))
                    }
                  >
                    {busy ? "处理中…" : "预览独立采集计划"}
                  </button>
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={busy}
                    onClick={() => void saveSelection()}
                  >
                    保存选择
                  </button>
                  {savedSelection && (
                    <p role="status" className="data-footnote">
                      已为当前用户和工作空间保存选择，刷新后可恢复。
                    </p>
                  )}
                </div>
              </form>
            )}
            {!canManage && (
              <p className="data-footnote">
                当前为只读访问，可以查看进度和产物。
              </p>
            )}
            {canManage && !available && (
              <p className="collector-form-unavailable">
                采集服务连接后，可在此创建和预览采集计划。
              </p>
            )}
          </div>
          <aside className="collector-sidebar" aria-label="计划预览与最近任务">
            {!preview && (
              <div className="collector-sidebar-empty">
                <h3>计划摘要</h3>
                选择数据范围后生成预览。确认新增分段和覆盖情况，再提交任务。
              </div>
            )}
            {preview && (
              <div className="collector-preview" aria-label="独立采集计划">
                <div className="collector-zone-heading compact">
                  <div>
                    <span className="collector-step-number">02</span>
                    <div>
                      <h3>
                        {preview.missing_parts === 0
                          ? "所选范围已有数据或任务"
                          : "下载计划已生成"}
                      </h3>
                      <p>核对范围和新增任务后确认提交</p>
                    </div>
                  </div>
                </div>
                <p>
                  {Object.entries(preview.instrument_names)
                    .slice(0, 8)
                    .map(([code, name]) => `${name} · ${code}`)
                    .join("，")}
                  {preview.request.instrument_ids.length > 8
                    ? ` 等 ${preview.request.instrument_ids.length} 个标的`
                    : ""}
                </p>
                <p className="data-secondary">
                  {catalog.find(
                    (item) => item.source === preview.request.source,
                  )?.name ?? preview.request.source}
                  {" · "}
                  {periods[preview.request.granularity] ??
                    preview.request.granularity}
                  {" · UTC 半开范围："}
                  {preview.request.start} — {preview.request.end}
                </p>
                {preview.field_definitions.length > 0 && (
                  <p className="data-secondary">
                    因子：
                    {preview.field_definitions
                      .map((field) => field.name)
                      .join("、")}
                  </p>
                )}
                <div className="collector-metrics">
                  <span>
                    总分段 <strong>{preview.planned_parts}</strong>
                  </span>
                  <span>
                    已完成 <strong>{preview.completed_parts}</strong>
                  </span>
                  <span>
                    处理中 <strong>{preview.active_parts}</strong>
                  </span>
                  <span>
                    新增下载 <strong>{preview.missing_parts}</strong>
                  </span>
                </div>
                <p className="data-secondary">
                  {preview.request.mode === "all"
                    ? "将重新请求全部范围，并保留已有产物。"
                    : "复用已完成或处理中的范围，只采集缺失部分。"}
                </p>
                {preview.execution_estimate && (
                  <div aria-label="采集调用估计">
                    <p>
                      执行调用估计 {preview.execution_estimate.calls_min}–
                      {preview.execution_estimate.calls_max} 次 · 内部信用{" "}
                      {preview.execution_estimate.credits_min}–
                      {preview.execution_estimate.credits_max}
                    </p>
                    <p className="data-secondary">
                      {preview.execution_estimate.basis}
                    </p>
                  </div>
                )}
                {preview.plan_guard && (
                  <p className="data-secondary">
                    单次上限：{preview.plan_guard.max_instruments} 个标的 /{" "}
                    {preview.plan_guard.max_new_parts}{" "}
                    个新增分段。确认不能绕过硬预算和规模限制。
                  </p>
                )}
                {preview.confirmation_required && (
                  <label>
                    <input
                      type="checkbox"
                      aria-label="确认计划调用规模"
                      checked={planConfirmed}
                      onChange={(event) =>
                        setPlanConfirmed(event.target.checked)
                      }
                    />
                    我已核对新增任务及调用区间，确认执行；实际重试可能增加用量。
                  </label>
                )}
                <button
                  className="button button-primary"
                  disabled={
                    busy ||
                    Boolean(preview.confirmation_required && !planConfirmed)
                  }
                  onClick={() => void submit()}
                >
                  提交独立采集任务
                </button>
              </div>
            )}
            {jobs.length > 0 && (
              <details className="collector-recent">
                <summary>
                  <span>最近任务</span>
                  <span className="collector-history-count">{jobs.length}</span>
                </summary>
                <ul>
                  {jobs.map((item) => (
                    <li key={item.job_id}>
                      <button
                        className="button button-quiet"
                        onClick={() => void refresh(item.job_id)}
                      >
                        {Object.values(item.instrument_names)
                          .slice(0, 2)
                          .join("、")}{" "}
                        · {item.request.granularity} ·{" "}
                        {states[item.state] ?? item.state}
                      </button>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </aside>
        </div>
      </div>
      <div className={workspaceStyles.view} hidden={view !== "results"}>
        <div className={workspaceStyles.taskPicker}>
          <label>
            查看已保存任务
            <select
              aria-label="查看已保存采集任务"
              value={job?.job_id ?? ""}
              onChange={(event) => {
                if (event.target.value) void refresh(event.target.value);
              }}
            >
              <option value="" disabled>
                请选择任务
              </option>
              {job && !jobs.some((item) => item.job_id === job.job_id) && (
                <option value={job.job_id}>
                  当前任务 · {job.job_id.slice(0, 8)}
                </option>
              )}
              {jobs.map((item) => (
                <option key={item.job_id} value={item.job_id}>
                  {Object.values(item.instrument_names)
                    .slice(0, 2)
                    .join("、") || item.job_id.slice(0, 8)}
                  {" · "}
                  {periods[item.request.granularity] ??
                    item.request.granularity}
                  {" · "}
                  {states[item.state] ?? item.state}
                  {" · "}
                  {item.job_id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          <p>选择任务查看分段、质量和已发布记录，刷新后仍可继续查看。</p>
        </div>
        {!job && (
          <p className="data-footnote" role="status">
            {jobs.length
              ? "请选择一个已保存任务。"
              : "当前工作区暂无采集任务。"}
            {!canManage && " 当前为只读访问，可以查看进度和产物。"}
          </p>
        )}
        <div className={workspaceStyles.resultGrid}>
          {job && (
            <div className="collector-run" data-collector-job={job.job_id}>
              <div className="data-section-heading">
                <div className="collector-zone-heading compact">
                  <div>
                    <span className="collector-step-number">03</span>
                    <div>
                      <h3>任务执行</h3>
                      <p>
                        {states[job.state] ?? job.state} · {job.total_parts}{" "}
                        个分段
                      </p>
                    </div>
                  </div>
                </div>
                <button
                  className="button button-secondary"
                  onClick={() => void refresh(job.job_id, job.offset)}
                >
                  刷新采集进度
                </button>
              </div>
              <p
                className="data-footnote"
                role={jobReadError ? "alert" : "status"}
              >
                {jobReadError
                  ? `${errors[jobReadError] ?? "进度读取失败。"} 当前显示上次成功快照；${pending ? "自动刷新将退避重试，也可" : "可"}点击“刷新采集进度”重试。`
                  : pending
                    ? "自动刷新已开启，失败后将退避重试。"
                    : ""}
                {lastJobUpdate !== null &&
                  ` 上次更新：${new Intl.DateTimeFormat("zh-CN", {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                    hour12: false,
                  }).format(lastJobUpdate)}。`}
              </p>
              <p>
                {Object.entries(job.instrument_names)
                  .slice(0, 6)
                  .map(([code, name]) => `${name} · ${code}`)
                  .join("，")}{" "}
                · {periods[job.request.granularity] ?? job.request.granularity}
              </p>
              <p className="data-secondary">
                关联 ID：<code>{job.request.correlation_id}</code> ·{" "}
                {job.request.source}
              </p>
              <div className="collector-metrics">
                <span>
                  总分段 <strong>{job.total_parts}</strong>
                </span>
                <span>
                  采集完成 <strong>{job.counts.completed ?? 0}</strong>
                </span>
                <span>
                  失败 <strong>{job.counts.failed ?? 0}</strong>
                </span>
                <span>
                  无观测 <strong>{job.counts.no_data ?? 0}</strong>
                </span>
              </div>
              {canManage && (
                <div className="data-actions">
                  {["running", "waiting"].includes(job.state) && (
                    <button
                      className="button button-secondary"
                      disabled={busy}
                      onClick={() => void control("cancel")}
                    >
                      取消独立任务
                    </button>
                  )}
                  {[
                    "failed",
                    "no_data",
                    "completed_with_gaps",
                    "cancelled",
                  ].includes(job.state) && (
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() => void control("retry")}
                    >
                      重试失败或空分段
                    </button>
                  )}
                  {job.parts.some(
                    (p) => p.state === "waiting" && p.wait_reason,
                  ) && (
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() => void control("retry")}
                    >
                      提前重试预算等待
                    </button>
                  )}
                </div>
              )}
              <div
                className={`collector-parts ${workspaceStyles.parts}`}
                role="region"
                aria-label="采集任务分段"
                tabIndex={0}
              >
                {job.parts.map((part) => (
                  <article
                    key={part.part_id}
                    className="collector-part"
                    data-collector-part={part.part_id}
                  >
                    <div>
                      <strong>
                        {part.instrument_name}{" "}
                        <small>
                          {part.instrument_id}
                          {part.field
                            ? ` · ${job.field_definitions.find((f) => f.factor_id === part.field)?.name ?? part.field}`
                            : ""}
                        </small>
                      </strong>
                      <span className={`data-status status-${part.state}`}>
                        {part.wait_reason && part.state === "waiting"
                          ? "等待预算 / 预留"
                          : (states[part.state] ?? part.state)}
                      </span>
                    </div>
                    <p className="data-secondary">
                      {part.start.slice(0, 10)} — {part.end.slice(0, 10)} UTC ·
                      尝试 {part.attempt} 次
                    </p>
                    {part.error_code && (
                      <p role="status">
                        {part.wait_reason
                          ? part.state === "waiting"
                            ? budgetWaits[part.wait_reason]
                            : "预算拒绝，当前没有自动重试时间；请核对额度配置后手动重试。"
                          : (errors[part.error_code] ?? part.error_code)}
                        {part.state === "waiting"
                          ? ` 下次重试：${part.next_attempt_at}`
                          : ""}
                      </p>
                    )}
                    {part.execution_kind === "normalization_replay" && (
                      <p className="data-footnote">
                        离线重处理 · 原始获取时间保持，未重新访问供应商。
                      </p>
                    )}
                    <CollectionQualityPanel
                      job={job.job_id}
                      part={part.part_id}
                    />
                    <CollectionReplayControl
                      key={`${job.job_id}:${part.part_id}`}
                      job={job.job_id}
                      part={part.part_id}
                      canManage={canManage}
                      offline={part.execution_kind === "normalization_replay"}
                    />
                    {part.publication && (
                      <>
                        <p>
                          {part.publication.record_count.toLocaleString()}{" "}
                          条标准记录 ·{" "}
                          {part.publication.raw_record_count.toLocaleString()}{" "}
                          条原始记录
                        </p>
                        {part.publication.evidence
                          ?.opening_boundary_semantics === "unverified" && (
                          <p role="status" className="data-footnote">
                            1 分钟来源含额外09:30观测，既有标签转换后为09:29；
                            时段语义未确认，不能据此认证为常规一分钟区间。
                          </p>
                        )}
                        {part.publication.dataset_kind === "bars" &&
                          part.publication.granularity === "1d" && (
                            <FreshnessPanel
                              publication={part.publication.publication_id}
                              canManage={canManage}
                            />
                          )}
                        <p className="data-secondary">
                          归档 SHA-256：
                          {part.publication.raw_sha256.slice(0, 16)}…
                        </p>
                        <div className="data-actions">
                          <button
                            className="button button-secondary"
                            onClick={() => void browse(part.publication!)}
                          >
                            {part.publication.dataset_kind === "factors"
                              ? "查看因子数据"
                              : "查看行情记录"}
                          </button>
                          {part.publication.dataset_kind === "bars" &&
                            (part.consumer?.state === "visible" ? (
                              <Link
                                className="button"
                                href={`/workspace/market?instrument=${encodeURIComponent(part.instrument_id)}&granularity=${job.request.granularity}${part.consumer.dataset_id ? `&dataset_id=${encodeURIComponent(part.consumer.dataset_id)}` : ""}`}
                              >
                                终端可见 · 查看图表
                              </Link>
                            ) : (
                              <span
                                className={`data-notice ${part.consumer?.state === "failed" ? "" : "warning"}`}
                              >
                                {part.consumer?.state === "failed"
                                  ? `市场导入失败 · ${part.consumer.error_code}，后台继续重试`
                                  : "采集完成 · 等待市场导入"}
                              </span>
                            ))}
                        </div>
                      </>
                    )}
                  </article>
                ))}
              </div>
              <nav
                className="universe-pagination"
                aria-label="独立采集分段分页"
              >
                {job.offset > 0 && (
                  <button
                    className="button"
                    onClick={() =>
                      void refresh(job.job_id, Math.max(0, job.offset - 100))
                    }
                  >
                    上一页分段
                  </button>
                )}
                {job.next_offset !== null && (
                  <button
                    className="button"
                    onClick={() => void refresh(job.job_id, job.next_offset!)}
                  >
                    下一页分段
                  </button>
                )}
              </nav>
            </div>
          )}
          {publication && (
            <div className="collector-records" aria-label="采集数据预览">
              <div className="collector-zone-heading compact">
                <div>
                  <span className="collector-step-number">04</span>
                  <div>
                    <h3>
                      {publication.instrument_name} ·{" "}
                      {publication.instrument_id} ·{" "}
                      {publication.field ?? periods[publication.granularity]}
                    </h3>
                    <p>已发布记录样本</p>
                  </div>
                </div>
              </div>
              <p className="page-description">
                {publication.limitations.join(" ")}
              </p>
              <div
                className={`collector-table-scroll ${workspaceStyles.records}`}
                tabIndex={0}
                aria-label="采集记录表格"
              >
                <table>
                  <thead>
                    <tr>
                      <th>日期 / 时间</th>
                      <th>
                        {publication.dataset_kind === "factors"
                          ? "因子值"
                          : "收盘价"}
                      </th>
                      <th>
                        {publication.dataset_kind === "factors"
                          ? "值状态 / 计算版本"
                          : "成交量（股）"}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((record, index) => (
                      <tr key={index}>
                        <td>{record.trade_date ?? record.timestamp}</td>
                        <td>
                          {publication.dataset_kind === "factors"
                            ? (record.value ?? "空值")
                            : record.close}
                        </td>
                        <td>
                          {publication.dataset_kind === "factors"
                            ? `${record.value_kind === "missing" ? (record.missing_reason === "not_available" ? "来源未开放" : "来源空值") : "来源观测"} / ${record.calc_version}`
                            : record.volume}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {nextRecordOffset !== null && (
                <button
                  className="button"
                  onClick={() => void browse(publication, nextRecordOffset)}
                >
                  下一页记录
                </button>
              )}
              <button
                className="button button-quiet"
                onClick={() => setPublication(null)}
              >
                关闭数据预览
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
