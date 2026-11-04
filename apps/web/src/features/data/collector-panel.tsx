"use client";

import workspaceStyles from "./collector-workspace.module.css";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
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
import { CollectorCreateForm } from "./collector-create-form";
import { CollectorPlanPreview } from "./collector-plan-preview";
import { CollectorJobDetail } from "./collector-job-detail";
import { PERIOD_ORDER, errors, preferredSource } from "./collector-labels";
import { WORKBENCH_NAVIGATION_EVENT } from "./workbench-tabs";
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
  window.addEventListener(WORKBENCH_NAVIGATION_EVENT, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(WORKBENCH_NAVIGATION_EVENT, onChange);
  };
}

/** Split-mode composition API: one mounted instance feeds the create half to
 * the download view and the results half to the jobs view, so switching
 * top-level views can never discard drafts or interrupt job polling. */
export type CollectorSplitApi = {
  create: ReactNode;
  results: ReactNode;
  view: "create" | "results";
  navigate: (next: "create" | "results") => void;
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
  children = null,
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
  children?: ((api: CollectorSplitApi) => ReactNode) | null;
}) {
  // Presence of a composition function is the split contract: presentation is
  // delegated to the caller (four-view workbench); absent, this panel renders
  // its historic combined layout with the internal view switcher.
  const split = typeof children === "function";
  const [catalogState, setCatalogState] = useState(factorCatalog);
  const displayedFactors = catalogState?.definitions ?? factors;
  const factorSelectionAvailable = catalogState
    ? catalogState.definitions.length > 0
    : factorsAvailable;
  const [kind, setKind] = useState<"bars" | "factors">(
    initialPreferences?.dataset_kind ?? "bars",
  );
  const [period, setPeriod] = useState(initialPreferences?.granularity ?? "1d");
  const [sourceId, setSourceId] = useState(() => {
    const initialKind = initialPreferences?.dataset_kind ?? "bars";
    const list = catalog.filter((item) => item.dataset_kind === initialKind);
    const effectivePeriod = initialKind === "factors" ? "1d" : period;
    const saved = initialPreferences?.source;
    if (saved) {
      const savedSource = list.find((item) => item.source === saved);
      // Dangling saved source stays selected so the picker can disclose it
      // instead of silently substituting a different source.
      if (!savedSource) return saved;
      if (
        savedSource.configured &&
        (savedSource.periods.includes(effectivePeriod) || list.length === 1)
      )
        return saved;
    }
    return (
      preferredSource(list, effectivePeriod)?.source ??
      saved ??
      list[0]?.source ??
      ""
    );
  });
  const sources = catalog.filter((item) => item.dataset_kind === kind);
  const source = sourceId
    ? sources.find((item) => item.source === sourceId)
    : sources[0];
  // An explicit user/preference choice stops the paid-first default from
  // re-selecting the source on every kind/period change; untouched selections
  // keep following the default.
  const sourceTouched = useRef(Boolean(initialPreferences?.source));
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
      if (!canManage) return "results";
      if (split) {
        // The top-level view key owns navigation in split mode; legacy
        // parameters still resolve so old deep links keep their meaning.
        const dataView = query.get("data_view");
        if (dataView === "jobs") return "results";
        if (dataView === "download") return "create";
      }
      return query.get("collect_view") === "results" ||
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
    if (split) {
      url.searchParams.set(
        "data_view",
        next === "create" ? "download" : "jobs",
      );
      url.searchParams.delete("collect_view");
    } else {
      url.searchParams.set("collect_view", next);
    }
    history.replaceState(null, "", url);
    window.dispatchEvent(new Event(WORKBENCH_NAVIGATION_EVENT));
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const errorRef = useRef<HTMLParagraphElement | null>(null);
  useEffect(() => {
    // Rejection feedback must reach the user where they are: the create view
    // shows it beside the action buttons, the results view in the panel
    // banner; either way it scrolls into sight instead of failing silently
    // somewhere above the viewport.
    if (error)
      errorRef.current?.scrollIntoView({
        block: "nearest",
        behavior: "smooth",
      });
  }, [error]);
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
    if (split) {
      url.searchParams.set("data_view", "jobs");
      url.searchParams.delete("collect_view");
    } else {
      url.searchParams.set("collect_view", "results");
    }
    history.replaceState(null, "", url);
    window.dispatchEvent(new Event(WORKBENCH_NAVIGATION_EVENT));
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
  const poolNameCounts = new Map<string, number>();
  for (const item of snapshots)
    poolNameCounts.set(item.name, (poolNameCounts.get(item.name) ?? 0) + 1);
  const duplicatePoolNames = new Set(
    [...poolNameCounts].filter(([, count]) => count > 1).map(([name]) => name),
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
  const startDay = Date.parse(`${first}T00:00:00Z`);
  const endDay = Date.parse(`${last}T00:00:00Z`);
  const dayCount =
    Number.isFinite(startDay) && Number.isFinite(endDay) && endDay >= startDay
      ? Math.floor((endDay - startDay) / 86_400_000) + 1
      : 0;
  // Mirror the provider strategy: daily and Pro history are organized by
  // calendar year, while ordinary intraday history caps each part at 7 days.
  const annual = period === "1d" || source?.source === "mairui.pro.bars";
  const firstYear = Number(first.slice(0, 4));
  const lastYear = Number(last.slice(0, 4));
  const yearCount =
    first && last && lastYear >= firstYear && lastYear - firstYear < 1000
      ? lastYear - firstYear + 1
      : 0;
  const segmentsPerInstrument = annual
    ? yearCount
    : dayCount
      ? Math.max(1, Math.ceil(dayCount / 7))
      : 0;
  const estimatedSegments = combinedIds.size * segmentsPerInstrument;
  const proMinuteSource = sources.find(
    (item) => item.source === "mairui.pro.bars" && item.configured,
  );
  // Union of what any source of this kind offers so a period switch can drive
  // the paid-first source auto-selection instead of being capped by the
  // currently selected source.
  const periodOptions =
    kind === "factors"
      ? ["1d"]
      : PERIOD_ORDER.filter((value) =>
          sources.some((item) => item.periods.includes(value)),
        );

  const connection = (
    <span
      className={`data-chip collector-connection ${available ? "is-connected" : "is-disconnected"}`}
    >
      <span aria-hidden="true" />
      {available ? "远程服务已连接" : "服务待连接"}
    </span>
  );
  const notices = (
    <>
      {!preferencesAvailable && (
        <p className="data-footnote" role="status">
          已保存选择暂不可读取，当前显示默认输入。请刷新重试或检查业务
          API；保存新选择会覆盖原草稿。
        </p>
      )}
      {!available && (
        <p className="data-notice" role="alert">
          {split
            ? "独立采集服务尚未连接。已保存任务仍可在「任务与结果」查看。"
            : "独立采集服务尚未连接。旧任务仍在下方保留。"}
        </p>
      )}
    </>
  );
  // Exactly one of the two error channels renders at a time: inline beside the
  // create actions, or as the results banner. Split mode keeps the rule.
  const banner =
    error && view !== "create" ? (
      <p className="data-notice" role="alert" ref={errorRef}>
        {errors[error] ?? "操作暂未完成，请刷新重试。"}
      </p>
    ) : null;
  const createInner = (
    <div className={`collector-layout ${workspaceStyles.createLayout}`}>
      <CollectorCreateForm
        sources={sources}
        source={source}
        sourceId={sourceId}
        kind={kind}
        period={period}
        periodOptions={periodOptions}
        onKindChange={(nextKind) => {
          setKind(nextKind);
          // Re-run the paid-first default for the new kind instead
          // of leaving the source dangling on a filtered-out pick.
          const list = catalog.filter((item) => item.dataset_kind === nextKind);
          setSourceId(
            preferredSource(list, nextKind === "factors" ? "1d" : period)
              ?.source ?? "",
          );
        }}
        onSourceChange={(next) => {
          sourceTouched.current = true;
          setSourceId(next);
        }}
        onPeriodChange={(next) => {
          setPeriod(next);
          // The period drives capability: an untouched source keeps
          // following the paid-first default, and an explicit pick
          // only yields when it cannot serve the new period.
          const supporting = sources.filter(
            (item) => item.configured && item.periods.includes(next),
          );
          const best =
            supporting.find((item) => item.tier === "paid") ?? supporting[0];
          if (
            best &&
            (!source?.periods.includes(next) || !sourceTouched.current)
          )
            setSourceId(best.source);
        }}
        snapshots={snapshots}
        snapshotIds={snapshotIds}
        onSnapshotToggle={(snapshotId, checked) => {
          invalidateSelection();
          setSnapshotIds((current) =>
            checked
              ? [...new Set([...current, snapshotId])]
              : current.filter((id) => id !== snapshotId),
          );
        }}
        duplicatePoolNames={duplicatePoolNames}
        unresolvedPools={unresolvedPools}
        instruments={instruments}
        onInstrumentsChange={setInstruments}
        poolCount={selectedPools.length}
        manualCount={manualIds.length}
        combinedCount={combinedIds.size}
        estimatedSegments={estimatedSegments}
        segmentsPerInstrument={segmentsPerInstrument}
        annual={annual}
        proMinuteSource={proMinuteSource}
        onSwitchPro={() => {
          if (!proMinuteSource) return;
          invalidateSelection();
          sourceTouched.current = true;
          setSourceId(proMinuteSource.source);
        }}
        first={first}
        last={last}
        onFirstChange={setFirst}
        onLastChange={setLast}
        mode={mode}
        onModeChange={setMode}
        busy={busy}
        defaultRange={defaultRange}
        onFillDefaultDates={() => void fillDefaultDates()}
        catalogState={catalogState}
        onCatalogUpdated={(snapshot) => {
          if (catalogState?.content_sha256 !== snapshot.content_sha256)
            invalidateSelection();
          setCatalogState(snapshot);
        }}
        factorSelectionAvailable={factorSelectionAvailable}
        displayedFactors={displayedFactors}
        selectedFields={selectedFields}
        onFieldsChange={(fields) => {
          // Removing a chip emits no native change event. Invalidate
          // the frozen plan here too, before accepting new fields.
          invalidateSelection();
          setSelectedFields(fields);
        }}
        onFormChange={invalidateSelection}
        view={view}
        error={error}
        errorRef={errorRef}
        savedSelection={savedSelection}
        onSaveSelection={() => void saveSelection()}
        onPreview={(form) => void build(form)}
        canManage={canManage}
        available={available}
      />
      <CollectorPlanPreview
        preview={preview}
        catalog={catalog}
        planConfirmed={planConfirmed}
        onPlanConfirmedChange={setPlanConfirmed}
        busy={busy}
        onSubmit={() => void submit()}
        jobs={jobs}
        onRefreshJob={(jobId) => void refresh(jobId)}
      />
    </div>
  );
  const resultsInner = (
    <CollectorJobDetail
      job={job}
      jobs={jobs}
      canManage={canManage}
      busy={busy}
      jobReadError={jobReadError}
      pending={pending}
      lastJobUpdate={lastJobUpdate}
      onRefresh={(jobId, offset) => void refresh(jobId, offset)}
      onControl={(action) => void control(action)}
      publication={publication}
      records={records}
      nextRecordOffset={nextRecordOffset}
      onBrowse={(value, offset) => void browse(value, offset)}
      onClosePublication={() => setPublication(null)}
    />
  );

  if (typeof children === "function")
    return (
      <>
        {children({
          create: (
            <section
              className={`panel collector-workbench ${workspaceStyles.workbench}`}
              id="independent-collector"
              aria-labelledby="collector-title"
            >
              <div className={workspaceStyles.heading}>
                <div>
                  <h2 id="collector-title">独立数据采集</h2>
                </div>
                {connection}
              </div>
              {notices}
              {createInner}
            </section>
          ),
          results: (
            <section
              className={`panel collector-workbench ${workspaceStyles.workbench}`}
              id="collector-results"
              aria-labelledby="collector-results-title"
            >
              <div className={workspaceStyles.heading}>
                <div>
                  <h2 id="collector-results-title">采集任务与产物</h2>
                </div>
                {connection}
              </div>
              {banner}
              <div className={workspaceStyles.view}>{resultsInner}</div>
            </section>
          ),
          view,
          navigate,
        })}
      </>
    );

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
        {connection}
      </div>
      {notices}
      {banner}
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
        {createInner}
      </div>
      <div className={workspaceStyles.view} hidden={view !== "results"}>
        {resultsInner}
      </div>
    </section>
  );
}
