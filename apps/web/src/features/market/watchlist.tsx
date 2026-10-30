"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import type { MarketInstrument, MarketSnapshot } from "../auth/server";
import { loadMarketSnapshots } from "../../app/workspace/market-watch-actions";
import { MarketChart } from "./chart";
import { MarketStreamStatus } from "./stream-status";

type Column = "market" | "exchange" | "price" | "change" | "volume";
type Preferences = {
  instrumentIds: string[];
  columns: Column[];
  sort: Column | "instrument_id";
};
const STORAGE_KEY = "qtrade.market-watch.v1";
const defaultColumns: Column[] = [
  "market",
  "exchange",
  "price",
  "change",
  "volume",
];
const labels: Record<Column, string> = {
  market: "市场",
  exchange: "交易所",
  price: "最新价",
  change: "涨跌幅",
  volume: "成交量",
};

const preferenceEvent = "qtrade-market-watch-change";
function subscribePreferences(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener(preferenceEvent, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(preferenceEvent, callback);
  };
}
function readPreferences() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}
function parsePreferences(
  raw: string | null,
  allowedIds: Set<string>,
): Preferences {
  if (!raw)
    return {
      instrumentIds: [],
      columns: defaultColumns,
      sort: "instrument_id",
    };
  try {
    const parsed = JSON.parse(raw) as Partial<Preferences>;
    return {
      instrumentIds: Array.isArray(parsed.instrumentIds)
        ? [
            ...new Set(
              parsed.instrumentIds.filter(
                (id): id is string =>
                  typeof id === "string" && allowedIds.has(id),
              ),
            ),
          ].slice(0, 50)
        : [],
      columns: Array.isArray(parsed.columns)
        ? [
            ...new Set(
              parsed.columns.filter(
                (column): column is Column =>
                  typeof column === "string" && Object.hasOwn(labels, column),
              ),
            ),
          ]
        : defaultColumns,
      sort:
        parsed.sort === "instrument_id" ||
        (typeof parsed.sort === "string" && Object.hasOwn(labels, parsed.sort))
          ? (parsed.sort as Preferences["sort"])
          : "instrument_id",
    };
  } catch {
    return {
      instrumentIds: [],
      columns: defaultColumns,
      sort: "instrument_id",
    };
  }
}

export function MarketWatch({
  instruments,
  catalogError,
  stream,
  initialInstrumentId,
  initialGranularity,
  initialDatasetId,
}: {
  instruments: MarketInstrument[];
  catalogError: boolean;
  stream: { workspaceId: string; websocketOrigin: string } | null;
  initialInstrumentId?: string;
  initialGranularity?: string;
  initialDatasetId?: string;
}) {
  const rawPreferences = useSyncExternalStore(
    subscribePreferences,
    readPreferences,
    () => null,
  );
  const handledInitialInstrument = useRef<string | null>(null);
  const [selected, setSelected] = useState("");
  const [chartInstrumentId, setChartInstrumentId] = useState<string | null>(
    null,
  );
  const [dismissedInitial, setDismissedInitial] = useState(false);
  const [snapshotResult, setSnapshotResult] = useState<{
    key: string;
    snapshots: MarketSnapshot[];
    error: boolean;
  }>({ key: "", snapshots: [], error: false });
  const byId = useMemo(
    () =>
      new Map(
        instruments.map((instrument) => [instrument.instrument_id, instrument]),
      ),
    [instruments],
  );

  const preferences = useMemo(
    () => parsePreferences(rawPreferences, new Set(byId.keys())),
    [rawPreferences, byId],
  );

  const updatePreferences = useCallback(
    (update: (current: Preferences) => Preferences) => {
      try {
        const next = update(
          parsePreferences(readPreferences(), new Set(byId.keys())),
        );
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
        window.dispatchEvent(new Event(preferenceEvent));
      } catch {
        // Keep rendering when browser storage is unavailable.
      }
    },
    [byId],
  );

  useEffect(() => {
    if (
      !initialInstrumentId ||
      !byId.has(initialInstrumentId) ||
      handledInitialInstrument.current === initialInstrumentId
    )
      return;
    handledInitialInstrument.current = initialInstrumentId;
    if (!preferences.instrumentIds.includes(initialInstrumentId)) {
      updatePreferences((current) => ({
        ...current,
        instrumentIds: [...current.instrumentIds, initialInstrumentId].slice(
          0,
          50,
        ),
      }));
    }
  }, [initialInstrumentId, byId, preferences.instrumentIds, updatePreferences]);

  const activeChartInstrumentId =
    initialInstrumentId && !dismissedInitial && byId.has(initialInstrumentId)
      ? initialInstrumentId
      : chartInstrumentId;

  const [chartAvailability, setChartAvailability] = useState<{
    id: string;
    periods: string[];
    error: boolean;
  }>({ id: "", periods: [], error: false });
  useEffect(() => {
    if (!activeChartInstrumentId) return;
    let current = true;
    void loadMarketSnapshots([activeChartInstrumentId]).then((result) => {
      if (current)
        setChartAvailability({
          id: activeChartInstrumentId,
          periods: result.snapshots[0]?.available_granularities ?? [],
          error: result.error,
        });
    });
    return () => {
      current = false;
    };
  }, [activeChartInstrumentId]);
  const instrumentKey = preferences.instrumentIds.join("\n");
  useEffect(() => {
    let current = true;
    const ids = instrumentKey ? instrumentKey.split("\n") : [];
    if (ids.length === 0)
      return () => {
        current = false;
      };
    void loadMarketSnapshots(ids).then((result) => {
      if (!current) return;
      setSnapshotResult({ key: instrumentKey, ...result });
    });
    return () => {
      current = false;
    };
  }, [instrumentKey]);

  const snapshots =
    snapshotResult.key === instrumentKey ? snapshotResult.snapshots : [];
  const snapshotError =
    snapshotResult.key === instrumentKey && snapshotResult.error;
  const loading =
    preferences.instrumentIds.length > 0 &&
    snapshotResult.key !== instrumentKey;

  const ordered = [...preferences.instrumentIds].sort((a, b) => {
    if (preferences.sort === "instrument_id") return a.localeCompare(b);
    const quote = (id: string) =>
      snapshots.find((item) => item.instrument_id === id);
    const value = (id: string) => {
      const item = quote(id);
      if (!item?.latest) return Number.NEGATIVE_INFINITY;
      if (preferences.sort === "price") return Number(item.latest.close);
      if (preferences.sort === "volume") return Number(item.latest.volume);
      if (preferences.sort === "change") {
        const previous = Number(item.previous?.close);
        return previous
          ? (Number(item.latest.close) - previous) / previous
          : Number.NEGATIVE_INFINITY;
      }
      return byId.get(id)?.[preferences.sort] ?? "";
    };
    const left = value(a);
    const right = value(b);
    return typeof left === "number" && typeof right === "number"
      ? right - left || a.localeCompare(b)
      : String(left).localeCompare(String(right)) || a.localeCompare(b);
  });

  function addInstrument() {
    if (
      !selected ||
      preferences.instrumentIds.includes(selected) ||
      preferences.instrumentIds.length >= 50
    )
      return;
    updatePreferences((current) => ({
      ...current,
      instrumentIds: [...current.instrumentIds, selected],
    }));
    setSelected("");
  }
  function toggleColumn(column: Column) {
    updatePreferences((current) => ({
      ...current,
      columns: current.columns.includes(column)
        ? current.columns.filter((item) => item !== column)
        : [...current.columns, column],
    }));
  }
  function sortBy(column: Preferences["sort"]) {
    updatePreferences((current) => ({ ...current, sort: column }));
  }

  return (
    <div className="workspace-page market-watch-page">
      <div className="page-heading market-heading">
        <div>
          <p className="eyebrow">MARKET WATCH</p>
          <h1>市场终端</h1>
          <p className="page-description">
            关注标的与最近已发布行情。自选列表、排序和列配置会保存在此浏览器中。
          </p>
        </div>
        <span className="market-session">
          <i aria-hidden="true" />
          历史行情快照
        </span>
        {stream ? (
          <MarketStreamStatus
            workspaceId={stream.workspaceId}
            websocketOrigin={stream.websocketOrigin}
          />
        ) : (
          <p className="market-stream-status unconfigured" role="status">
            <span aria-hidden="true" />
            实时推送端点未配置
          </p>
        )}
      </div>
      {catalogError && (
        <p className="data-notice" role="alert">
          暂时无法读取标的目录，请稍后刷新。
        </p>
      )}
      {snapshotError && (
        <p className="data-notice" role="alert">
          行情快照暂时不可用，自选列表仍已保留。
        </p>
      )}
      <div className="market-toolbar panel">
        <div className="market-add">
          <label htmlFor="watch-instrument">添加关注标的</label>
          <select
            id="watch-instrument"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            disabled={!instruments.length}
          >
            <option value="">选择标的</option>
            {instruments
              .filter(
                (item) =>
                  !preferences.instrumentIds.includes(item.instrument_id),
              )
              .map((item) => (
                <option value={item.instrument_id} key={item.instrument_id}>
                  {item.instrument_id} · {item.name}
                </option>
              ))}
          </select>
          <button
            className="button button-primary"
            type="button"
            onClick={addInstrument}
            disabled={!selected || preferences.instrumentIds.length >= 50}
          >
            加入自选
          </button>
        </div>
        <details className="market-columns">
          <summary>自定义列</summary>
          <fieldset>
            <legend>选择表格列</legend>
            {defaultColumns.map((column) => (
              <label key={column}>
                <input
                  type="checkbox"
                  checked={preferences.columns.includes(column)}
                  onChange={() => toggleColumn(column)}
                />
                {labels[column]}
              </label>
            ))}
          </fieldset>
        </details>
      </div>
      {activeChartInstrumentId &&
        byId.has(activeChartInstrumentId) &&
        chartAvailability.id === activeChartInstrumentId &&
        !chartAvailability.error && (
          <MarketChart
            key={`${activeChartInstrumentId}/${activeChartInstrumentId === initialInstrumentId ? (initialDatasetId ?? "latest") : "latest"}`}
            datasetId={
              activeChartInstrumentId === initialInstrumentId
                ? initialDatasetId
                : undefined
            }
            preferredGranularity={initialGranularity}
            availableGranularities={chartAvailability.periods}
            instrumentId={activeChartInstrumentId}
            name={byId.get(activeChartInstrumentId)?.name ?? ""}
            pricePrecision={
              byId.get(activeChartInstrumentId)?.price_precision ?? 4
            }
            onClose={() => {
              setChartInstrumentId(null);
              setDismissedInitial(true);
            }}
          />
        )}
      <section className="panel market-table-panel" aria-label="自选行情列表">
        <div className="market-table-top">
          <div>
            <p className="eyebrow">WATCHLIST</p>
            <h2>自选列表</h2>
          </div>
          <span>{preferences.instrumentIds.length} / 50</span>
        </div>
        {preferences.instrumentIds.length === 0 ? (
          <div className="market-empty">
            <span aria-hidden="true">◎</span>
            <strong>开始建立你的关注列表</strong>
            <p>
              {catalogError
                ? "标的目录暂不可用，请稍后刷新。"
                : instruments.length
                  ? "从已登记的标的中添加关注项，最新存储行情会显示在此处。"
                  : "当前工作区尚无已登记标的，请先导入 Instrument Master 数据。"}
            </p>
          </div>
        ) : (
          <div
            className="market-table-scroll"
            role="region"
            aria-label="自选行情表格"
            tabIndex={0}
          >
            <table className="market-table">
              <thead>
                <tr>
                  <th
                    aria-sort={
                      preferences.sort === "instrument_id"
                        ? "ascending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      onClick={() => sortBy("instrument_id")}
                    >
                      标的代码
                    </button>
                  </th>
                  {preferences.columns.map((column) => (
                    <th
                      key={column}
                      aria-sort={
                        preferences.sort === column ? "descending" : "none"
                      }
                    >
                      <button type="button" onClick={() => sortBy(column)}>
                        {labels[column]}
                      </button>
                    </th>
                  ))}
                  <th>
                    <span className="sr-only">操作</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {ordered.map((id) => {
                  const instrument = byId.get(id);
                  const quote = snapshots.find(
                    (item) => item.instrument_id === id,
                  );
                  const previous = Number(quote?.previous?.close);
                  const latest = Number(quote?.latest?.close);
                  const change = previous
                    ? ((latest - previous) / previous) * 100
                    : null;
                  return (
                    <tr key={id}>
                      <th scope="row">
                        <strong>{id}</strong>
                        <small>{instrument?.name ?? ""}</small>
                      </th>
                      {preferences.columns.includes("market") && (
                        <td>{instrument?.market ?? "—"}</td>
                      )}
                      {preferences.columns.includes("exchange") && (
                        <td>{instrument?.exchange ?? "—"}</td>
                      )}
                      {preferences.columns.includes("price") && (
                        <td className="market-number">
                          {quote?.latest
                            ? latest.toLocaleString("zh-CN", {
                                minimumFractionDigits: 0,
                                maximumFractionDigits:
                                  instrument?.price_precision ?? 4,
                              })
                            : "—"}
                          <small>
                            {quote?.latest
                              ? new Date(quote.latest.timestamp).toLocaleString(
                                  "zh-CN",
                                  { hour: "2-digit", minute: "2-digit" },
                                )
                              : loading
                                ? "读取中"
                                : "暂无行情"}
                          </small>
                        </td>
                      )}
                      {preferences.columns.includes("change") && (
                        <td
                          className={
                            change === null
                              ? "market-muted"
                              : change >= 0
                                ? "market-up"
                                : "market-down"
                          }
                        >
                          {change === null
                            ? "—"
                            : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}
                        </td>
                      )}
                      {preferences.columns.includes("volume") && (
                        <td className="market-number">
                          {quote?.latest
                            ? Number(quote.latest.volume).toLocaleString(
                                "zh-CN",
                              )
                            : "—"}
                        </td>
                      )}
                      <td>
                        <button
                          className="market-chart-open"
                          type="button"
                          aria-label={`显示 ${id} K 线图`}
                          onClick={() => {
                            setDismissedInitial(true);
                            setChartInstrumentId(id);
                          }}
                        >
                          图表
                        </button>
                        <button
                          className="market-remove"
                          type="button"
                          aria-label={`移除 ${id}`}
                          onClick={() =>
                            updatePreferences((current) => ({
                              ...current,
                              instrumentIds: current.instrumentIds.filter(
                                (item) => item !== id,
                              ),
                            }))
                          }
                        >
                          移除
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="market-footnote">
          数值来源于当前工作区最近发布的 Bar
          数据。实时订阅将在配置行情源后启用。
        </p>
      </section>
    </div>
  );
}
