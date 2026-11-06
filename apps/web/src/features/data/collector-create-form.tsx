"use client";

import type { MouseEvent, RefObject } from "react";
import { IsoDateInput } from "../../components/ui/iso-date";
import workspaceStyles from "./collector-workspace.module.css";
import { FactorFieldPicker } from "./factor-field-picker";
import { FactorCatalogStatus } from "./factor-catalog-status";
import { errors, observedDate, periods } from "./collector-labels";
import type {
  CollectorDefaultRange,
  CollectorSource,
  FactorDefinition,
  FactorCatalogSnapshot,
} from "./collector-types";
import type { UniverseSnapshot } from "./universe-server";

/** Step 01 presentation: scope/source/period configuration. All selection
 * logic (paid-first defaults, invalidation, touched tracking) stays in
 * CollectorPanel; this component renders state and forwards intents so the
 * create half can be mounted inside a different top-level view than results
 * without duplicating a single rule. */
export function CollectorCreateForm({
  sources,
  source,
  sourceId,
  kind,
  period,
  periodOptions,
  onKindChange,
  onSourceChange,
  onPeriodChange,
  snapshots,
  snapshotIds,
  onSnapshotToggle,
  duplicatePoolNames,
  unresolvedPools,
  instruments,
  onInstrumentsChange,
  poolCount,
  manualCount,
  combinedCount,
  estimatedSegments,
  segmentsPerInstrument,
  annual,
  proMinuteSource,
  onSwitchPro,
  first,
  last,
  onFirstChange,
  onLastChange,
  mode,
  onModeChange,
  busy,
  defaultRange,
  onFillDefaultDates,
  catalogState,
  onCatalogUpdated,
  factorSelectionAvailable,
  displayedFactors,
  selectedFields,
  onFieldsChange,
  onFormChange,
  view,
  error,
  errorRef,
  savedSelection,
  onSaveSelection,
  onPreview,
  canManage,
  available,
  onOpenDrawer,
  onDetachSnapshots,
  onGoToCatalog,
}: {
  sources: CollectorSource[];
  source: CollectorSource | undefined;
  sourceId: string;
  kind: "bars" | "factors";
  period: string;
  periodOptions: string[];
  onKindChange: (kind: "bars" | "factors") => void;
  onSourceChange: (sourceId: string) => void;
  onPeriodChange: (period: string) => void;
  snapshots: UniverseSnapshot[];
  snapshotIds: string[];
  onSnapshotToggle: (snapshotId: string, checked: boolean) => void;
  duplicatePoolNames: Set<string>;
  unresolvedPools: string[];
  instruments: string;
  onInstrumentsChange: (value: string) => void;
  poolCount: number;
  manualCount: number;
  combinedCount: number;
  estimatedSegments: number;
  segmentsPerInstrument: number;
  annual: boolean;
  proMinuteSource: CollectorSource | undefined;
  onSwitchPro: () => void;
  first: string;
  last: string;
  onFirstChange: (value: string) => void;
  onLastChange: (value: string) => void;
  mode: "missing" | "all";
  onModeChange: (mode: "missing" | "all") => void;
  busy: boolean;
  defaultRange: CollectorDefaultRange | null;
  onFillDefaultDates: () => void;
  catalogState: FactorCatalogSnapshot | null;
  onCatalogUpdated: (snapshot: FactorCatalogSnapshot) => void;
  factorSelectionAvailable: boolean;
  displayedFactors: FactorDefinition[];
  selectedFields: string[];
  onFieldsChange: (fields: string[]) => void;
  onFormChange: () => void;
  view: "create" | "results";
  error: string;
  errorRef: RefObject<HTMLParagraphElement | null>;
  savedSelection: boolean;
  onSaveSelection: () => void;
  onPreview: (form: FormData) => void;
  canManage: boolean;
  available: boolean;
  onOpenDrawer: (event: MouseEvent<HTMLButtonElement>) => void;
  onDetachSnapshots: () => void;
  onGoToCatalog: () => void;
}) {
  const selectedPools = snapshots.filter((item) =>
    snapshotIds.includes(item.snapshot_id),
  );
  return (
    <div className="collector-builder">
      {canManage && available && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onPreview(new FormData(event.currentTarget));
          }}
          onChange={onFormChange}
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
              data-scope-field="kind"
              value={kind}
              onChange={(e) =>
                onKindChange(e.target.value as "bars" | "factors")
              }
            >
              <option value="bars">价格行情 · 日线与分钟</option>
              <option value="factors">量化因子 · 历史序列</option>
            </select>
          </label>
          <label className="collector-source-field">
            数据来源
            <select
              aria-label="采集数据来源"
              data-scope-field="source"
              value={sourceId || source?.source || ""}
              onChange={(e) => onSourceChange(e.target.value)}
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
                  {!item.configured
                    ? " · 未配置"
                    : item.tier === "paid"
                      ? " · 付费"
                      : " · 免费"}
                </option>
              ))}
            </select>
          </label>
          <label className="collector-period-field">
            周期
            <select
              aria-label="独立采集周期"
              data-scope-field="period"
              value={kind === "factors" ? "1d" : period}
              onChange={(e) => onPeriodChange(e.target.value)}
            >
              {kind === "bars" && !periodOptions.includes(period) && (
                <option value={period}>
                  {periods[period] ?? period} · 当前来源不支持
                </option>
              )}
              {periodOptions.map((value) => (
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
            data-scope-group="inherited"
            data-scope-field="pools"
            tabIndex={-1}
          >
            <legend>
              继承自数据目录 · 冻结股票池（可多选）
              {snapshotIds.length > 0 && (
                <span className="collector-pool-selected">
                  已选 {snapshotIds.length}
                </span>
              )}
            </legend>
            <p>
              股票池成员以快照冻结提交；也可在“本次设置”追加公司代码，两者合并去重。
            </p>
            {selectedPools.length > 0 ? (
              <div
                className={workspaceStyles.inheritedChips}
                aria-label="已继承股票池"
              >
                {selectedPools.map((item) => (
                  <button
                    key={item.snapshot_id}
                    type="button"
                    onClick={() => onSnapshotToggle(item.snapshot_id, false)}
                    aria-label={`移除股票池 ${item.name}`}
                  >
                    {item.name} · v{item.version} ×
                  </button>
                ))}
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={onDetachSnapshots}
                >
                  解除继承
                </button>
              </div>
            ) : (
              <p className={workspaceStyles.inheritedEmpty}>
                <span>尚未继承股票池。</span>
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={onGoToCatalog}
                >
                  前往数据目录
                </button>
              </p>
            )}
            <div className="collector-pool-options">
              {snapshots.map((item) => {
                const selected = snapshotIds.includes(item.snapshot_id);
                return (
                  <label
                    key={item.snapshot_id}
                    className={selected ? "is-selected" : undefined}
                  >
                    <input
                      type="checkbox"
                      value={item.snapshot_id}
                      checked={selected}
                      onChange={(event) =>
                        onSnapshotToggle(item.snapshot_id, event.target.checked)
                      }
                    />
                    <span>
                      {item.name}
                      <small>
                        v{item.version} · {item.members.length} 个成员 ·{" "}
                        {observedDate(item.observed_at)}
                        {duplicatePoolNames.has(item.name)
                          ? ` · #${item.snapshot_id.slice(0, 8)}`
                          : ""}
                      </small>
                    </span>
                  </label>
                );
              })}
              {snapshots.length === 0 && <span>暂无已保存股票池</span>}
            </div>
            {unresolvedPools.length > 0 && (
              <p role="alert">
                已选 {unresolvedPools.length}{" "}
                个股票池不在当前列表，将保留选择但请确认可用性。
              </p>
            )}
          </fieldset>
          <fieldset
            className="collector-wide"
            aria-label="本次设置的手工标的"
            data-scope-group="explicit"
          >
            <legend>本次设置 · 手工标的</legend>
            <label>
              追加公司代码（可与上方股票池组合）
              <input
                name="instruments"
                value={instruments}
                onChange={(event) => onInstrumentsChange(event.target.value)}
                aria-label="独立采集标的"
                placeholder="601288.SH, 600519.SH；指数使用 index:000001.SH"
                maxLength={20000}
                data-scope-field="instruments"
              />
            </label>
            <button
              type="button"
              className="button button-secondary"
              onClick={onOpenDrawer}
            >
              从清单选择
            </button>
          </fieldset>
          <p
            className={`collector-wide collector-scope-note ${combinedCount ? "" : "is-empty"}`}
          >
            {combinedCount
              ? `本次合并范围：${poolCount} 个股票池、${manualCount} 个手工标的，去重后 ${combinedCount} 个标的。提交时冻结所选池成员。`
              : "尚未选择范围：勾选至少一个行业/指数池，或填写公司代码后才能预览。"}
          </p>
          {estimatedSegments > 0 && (
            <div className="collector-wide collector-segment-estimate">
              <strong>
                粗估约 {estimatedSegments.toLocaleString("zh-CN")} 个采集分段
              </strong>
              <span>
                {combinedCount} 个标的 × 每标的约{" "}
                {segmentsPerInstrument.toLocaleString("zh-CN")}{" "}
                段；最终以预览处理。
                {annual
                  ? " 当前来源按日历年组织分段。"
                  : " 普通历史分钟接口按最多 7 天拆分。"}
              </span>
              {!annual && proMinuteSource && (
                <p className="data-notice warning">
                  当前分钟范围会产生大量分段。可显式切换到 MaiRui Pro
                  分钟历史，按年组织范围通常显著减少分段。
                  <button
                    type="button"
                    className="button button-secondary"
                    onClick={onSwitchPro}
                  >
                    改用 Pro 分钟历史
                  </button>
                </p>
              )}
            </div>
          )}
          <div className="collector-form-section-title">
            <span>时间范围与下载策略</span>
          </div>
          <IsoDateInput
            className="collector-date-field"
            label={`开始日期（${source?.timezone ?? "UTC"}）`}
            ariaLabel="独立采集开始日期"
            name="first"
            value={first}
            onChange={onFirstChange}
            dataField="dates"
          />
          <IsoDateInput
            className="collector-date-field"
            label="结束日期（含当日）"
            ariaLabel="独立采集结束日期"
            name="last"
            value={last}
            onChange={onLastChange}
          />
          <label className="collector-mode-field">
            下载方式
            <select
              aria-label="独立采集下载方式"
              data-scope-field="mode"
              value={mode}
              onChange={(e) =>
                onModeChange(e.target.value as "missing" | "all")
              }
            >
              <option value="missing">仅下载缺失数据</option>
              <option value="all">重新下载全部数据</option>
            </select>
          </label>
          {kind === "bars" &&
            source?.source === "mairui.history.bars" &&
            period === "1d" && (
              <div className={`collector-wide ${workspaceStyles.dateShortcut}`}>
                <button
                  type="button"
                  className="button button-secondary"
                  disabled={
                    busy || Boolean(first || last) || !source.configured
                  }
                  onClick={onFillDefaultDates}
                >
                  {busy ? "计算中…" : "使用最近自然年日期"}
                </button>
                {!busy && !source.configured ? (
                  <small className="data-footnote">
                    当前来源未配置，无法计算默认区间。
                  </small>
                ) : (
                  !busy &&
                  Boolean(first || last) && (
                    <small className="data-footnote">
                      已填写日期；清空开始与结束日期后可一键填充最近自然年。
                    </small>
                  )
                )}
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
            <fieldset
              className="collector-wide collector-fields"
              data-scope-field="fields"
              tabIndex={-1}
            >
              <legend>选择历史因子</legend>
              <FactorCatalogStatus
                snapshot={catalogState}
                onUpdated={onCatalogUpdated}
              />
              {!factorSelectionAvailable && (
                <p role="alert">因子目录读取失败，当前无法创建因子任务。</p>
              )}
              <FactorFieldPicker
                definitions={displayedFactors}
                selected={selectedFields}
                onChange={onFieldsChange}
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
            {view === "create" && error && (
              <p className="data-notice" role="alert" ref={errorRef}>
                {errors[error] ?? "操作暂未完成，请刷新重试。"}
              </p>
            )}
            {!busy && !source?.configured && (
              <small className="data-footnote">
                当前来源未配置，无法预览采集计划。
              </small>
            )}
            {!busy &&
              source?.configured &&
              kind === "factors" &&
              (!factorSelectionAvailable || selectedFields.length === 0) && (
                <small className="data-footnote">
                  请至少选择一个因子字段后再预览。
                </small>
              )}
            <button
              type="submit"
              className="button button-primary"
              disabled={
                busy ||
                !source?.configured ||
                (kind === "factors" &&
                  (!factorSelectionAvailable || selectedFields.length === 0))
              }
            >
              {busy ? "处理中…" : "预览独立采集计划"}
            </button>
            <button
              type="button"
              className="button button-secondary"
              disabled={busy}
              onClick={onSaveSelection}
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
        <p className="data-footnote">当前为只读访问，可以查看进度和产物。</p>
      )}
      {canManage && !available && (
        <p className="collector-form-unavailable">
          采集服务连接后，可在此创建和预览采集计划。
        </p>
      )}
    </div>
  );
}
