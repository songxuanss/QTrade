"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { loadMarketChart } from "../../app/workspace/market-watch-actions";
import type { MarketChartBar, MarketChartIndicators } from "../auth/server";

const granularities = ["1m", "5m", "15m", "30m", "1h", "1d"] as const;
const periods = [
  { days: 1, label: "1 日" },
  { days: 7, label: "1 周" },
  { days: 30, label: "1 月" },
] as const;
const intervalSeconds: Record<(typeof granularities)[number], number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1h": 3600,
  "1d": 86400,
};

export function MarketChart({
  instrumentId,
  name,
  pricePrecision,
  availableGranularities = [],
  preferredGranularity,
  datasetId,
  onClose,
}: {
  instrumentId: string;
  name: string;
  pricePrecision: number;
  availableGranularities?: string[];
  preferredGranularity?: string;
  datasetId?: string;
  onClose: () => void;
}) {
  const defaultGranularity =
    granularities.find(
      (item) =>
        item === preferredGranularity && availableGranularities.includes(item),
    ) ??
    granularities.find((item) => availableGranularities.includes(item)) ??
    "1m";
  const [granularity, setGranularity] =
    useState<(typeof granularities)[number]>(defaultGranularity);
  const [days, setDays] = useState<(typeof periods)[number]["days"]>(
    defaultGranularity === "1d" ? 30 : 1,
  );
  const [result, setResult] = useState<{
    key: string;
    bars: MarketChartBar[];
    indicators: MarketChartIndicators | null;
    error: "none" | "missing" | "unavailable";
  }>({ key: "", bars: [], indicators: null, error: "none" });
  const [enabled, setEnabled] = useState({
    ma: true,
    ema: false,
    bollinger: false,
  });
  const key = `${instrumentId}/${granularity}/${days}/${datasetId ?? "latest"}`;
  useEffect(() => {
    let current = true;
    void loadMarketChart(instrumentId, granularity, days, datasetId).then(
      (response) => {
        if (current) setResult({ key, ...response });
      },
    );
    return () => {
      current = false;
    };
  }, [instrumentId, granularity, days, datasetId, key]);

  const bars = useMemo(
    () => (result.key === key ? result.bars : []),
    [result, key],
  );
  const visibleBars = useMemo(() => bars.slice(-2000), [bars]);
  const error = result.key === key ? result.error : "none";
  const indicators = result.key === key ? result.indicators : null;
  const loading = result.key !== key;
  const chart = useMemo(() => {
    if (!visibleBars.length) return null;
    const width = 1000;
    const top = 22;
    const priceHeight = 242;
    const volumeTop = 286;
    const volumeHeight = 70;
    const values = visibleBars.flatMap((bar) => [
      Number(bar.high),
      Number(bar.low),
    ]);
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const pad = Math.max((maximum - minimum) * 0.08, maximum * 0.001);
    const low = minimum - pad;
    const high = maximum + pad;
    const maxVolume = Math.max(
      ...visibleBars.map((bar) => Number(bar.volume)),
      1,
    );
    const step = width / visibleBars.length;
    const scaleY = (price: number) =>
      top + ((high - price) / (high - low)) * priceHeight;
    const threshold =
      intervalSeconds[granularity] * (granularity === "1d" ? 3 : 1.5);
    const gaps = visibleBars.flatMap((bar, index) => {
      if (index === 0) return [];
      const elapsed =
        (Date.parse(bar.timestamp) -
          Date.parse(visibleBars[index - 1].timestamp)) /
        1000;
      return elapsed > threshold ? [{ index, elapsed }] : [];
    });
    return {
      width,
      bars: visibleBars,
      top,
      priceHeight,
      volumeTop,
      volumeHeight,
      low,
      high,
      step,
      scaleY,
      maxVolume,
      gaps,
      threshold,
      priceTicks: [0, 1, 2, 3].map(
        (index) => high - ((high - low) * index) / 3,
      ),
      linePath: (values: (string | null)[] | undefined) => {
        if (!values) return "";
        const offset = bars.length - visibleBars.length;
        let connected = false;
        return values
          .slice(offset)
          .map((value, index) => {
            if (value === null) {
              connected = false;
              return "";
            }
            const x = step * index + step / 2;
            const y = scaleY(Number(value));
            const command = connected ? "L" : "M";
            connected = true;
            return `${command}${x},${y}`;
          })
          .filter(Boolean)
          .join(" ");
      },
    };
  }, [visibleBars, granularity, bars.length]);

  return (
    <section
      className="panel market-chart"
      aria-labelledby="market-chart-heading"
    >
      <div className="market-chart-header">
        <div>
          <p className="eyebrow">PRICE ACTION</p>
          <h2 id="market-chart-heading">
            {name} · {instrumentId} K 线图
          </h2>
          <p>{name} · OHLC 与成交量</p>
        </div>
        <button
          className="market-remove"
          type="button"
          onClick={onClose}
          aria-label="关闭图表"
        >
          关闭
        </button>
      </div>
      {datasetId && (
        <p className="data-footnote" role="status">
          当前查看冻结发布版本。{" "}
          <code style={{ overflowWrap: "anywhere" }}>{datasetId}</code>{" "}
          <Link
            href={`/workspace/market?instrument=${encodeURIComponent(instrumentId)}&granularity=${granularity}`}
          >
            查看最新行情
          </Link>
        </p>
      )}
      <div className="market-chart-controls">
        <div className="market-chart-selectors" aria-label="图表周期">
          {granularities.map((item) => (
            <button
              className={granularity === item ? "active" : ""}
              type="button"
              aria-pressed={granularity === item}
              disabled={Boolean(datasetId && item !== preferredGranularity)}
              key={item}
              onClick={() => setGranularity(item)}
            >
              {item}
            </button>
          ))}
        </div>
        <div className="market-chart-selectors" aria-label="显示范围">
          {periods.map((item) => (
            <button
              className={days === item.days ? "active" : ""}
              type="button"
              aria-pressed={days === item.days}
              key={item.days}
              onClick={() => setDays(item.days)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      {loading && (
        <p className="market-chart-state" role="status">
          正在读取已发布行情…
        </p>
      )}
      {!loading && error === "unavailable" && (
        <p className="market-chart-state error" role="alert">
          行情服务暂时不可用，请重试或稍后刷新。
        </p>
      )}
      {!loading && error === "missing" && (
        <div className="market-chart-state">
          <strong>该周期暂无可用行情数据</strong>
          <p>
            {datasetId
              ? "该发布版本在当前标的或周期下不可用。请核对链接，或主动切换最新行情。"
              : "请选择与下载任务一致的周期：已下载日线时请切换到1d，日线不会自动生成1m行情。Tick数据需先在数据管理中发布为分钟数据集。"}
          </p>
          <Link href="/workspace/data#batches">查看数据批次</Link>
        </div>
      )}
      {!loading && error === "none" && bars.length === 0 && (
        <p className="market-chart-state">当前时间范围暂无 Bar 数据。</p>
      )}
      {!loading && error === "none" && chart && (
        <>
          <div className="market-indicator-controls" aria-label="图表指标">
            {(
              [
                ["ma", "MA 20"],
                ["ema", "EMA 20"],
                ["bollinger", "BOLL 20,2"],
              ] as const
            ).map(([key, label]) => (
              <button
                type="button"
                aria-pressed={enabled[key]}
                className={enabled[key] ? "active" : ""}
                key={key}
                onClick={() =>
                  setEnabled((current) => ({
                    ...current,
                    [key]: !current[key],
                  }))
                }
              >
                {label}
              </button>
            ))}
          </div>
          <div className="market-chart-legend">
            <span>
              <i className="candle-up" />
              收盘高于开盘
            </span>
            <span>
              <i className="candle-down" />
              收盘低于开盘
            </span>
            <span>
              {bars.length.toLocaleString()} 根 Bar
              {bars.length > visibleBars.length
                ? ` · 图表显示最近 ${visibleBars.length.toLocaleString()} 根`
                : ""}
            </span>
          </div>
          <div
            className="market-svg-scroll"
            role="region"
            aria-label="OHLC 蜡烛图与成交量"
            tabIndex={0}
          >
            <svg
              className="market-candles"
              viewBox={`0 0 ${chart.width} 380`}
              role="img"
              aria-labelledby="chart-svg-title chart-svg-desc"
              preserveAspectRatio="none"
            >
              <title id="chart-svg-title">
                {instrumentId} {granularity} K 线与成交量
              </title>
              <desc id="chart-svg-desc">
                展示 {bars.length} 根 Bar。上方为 OHLC 蜡烛图，下方为成交量。
              </desc>
              {chart.priceTicks.map((price, index) => {
                const y = chart.scaleY(price);
                return (
                  <g key={index}>
                    <line
                      x1="0"
                      x2={chart.width}
                      y1={y}
                      y2={y}
                      className="chart-grid"
                    />
                    <text
                      x={chart.width - 3}
                      y={y - 4}
                      textAnchor="end"
                      className="chart-axis-label"
                    >
                      {price.toLocaleString("zh-CN", {
                        maximumFractionDigits: pricePrecision,
                      })}
                    </text>
                  </g>
                );
              })}
              <line
                x1="0"
                x2={chart.width}
                y1={chart.volumeTop - 8}
                y2={chart.volumeTop - 8}
                className="chart-grid"
              />
              {chart.bars.map((bar, index) => {
                const x = chart.step * index + chart.step / 2;
                const open = Number(bar.open);
                const close = Number(bar.close);
                const rising = close >= open;
                const colorClass = rising ? "candle-up" : "candle-down";
                const bodyTop = chart.scaleY(Math.max(open, close));
                const bodyBottom = chart.scaleY(Math.min(open, close));
                const candleWidth = Math.max(
                  2,
                  Math.min(12, chart.step * 0.58),
                );
                const volume = Number(bar.volume);
                return (
                  <g key={`${bar.timestamp}-${index}`}>
                    <line
                      x1={x}
                      x2={x}
                      y1={chart.scaleY(Number(bar.high))}
                      y2={chart.scaleY(Number(bar.low))}
                      className={colorClass}
                    />
                    <rect
                      x={x - candleWidth / 2}
                      y={bodyTop}
                      width={candleWidth}
                      height={Math.max(1.5, bodyBottom - bodyTop)}
                      className={colorClass}
                    />
                    <rect
                      x={x - candleWidth / 2}
                      y={
                        chart.volumeTop +
                        chart.volumeHeight * (1 - volume / chart.maxVolume)
                      }
                      width={candleWidth}
                      height={(chart.volumeHeight * volume) / chart.maxVolume}
                      className={`volume-bar ${colorClass}`}
                    />
                  </g>
                );
              })}
              {enabled.ma && indicators && (
                <path
                  d={chart.linePath(indicators.ma)}
                  className="indicator-line indicator-ma"
                />
              )}
              {enabled.ema && indicators && (
                <path
                  d={chart.linePath(indicators.ema)}
                  className="indicator-line indicator-ema"
                />
              )}
              {enabled.bollinger && indicators && (
                <>
                  <path
                    d={chart.linePath(indicators.bollinger_upper)}
                    className="indicator-line indicator-boll"
                  />
                  <path
                    d={chart.linePath(indicators.bollinger_middle)}
                    className="indicator-line indicator-boll-middle"
                  />
                  <path
                    d={chart.linePath(indicators.bollinger_lower)}
                    className="indicator-line indicator-boll"
                  />
                </>
              )}
              {chart.gaps.map(({ index, elapsed }) => {
                const x = chart.step * index;
                return (
                  <g key={`gap-${index}`}>
                    <line
                      x1={x}
                      x2={x}
                      y1={chart.top}
                      y2={chart.volumeTop + chart.volumeHeight}
                      className="chart-gap"
                    />
                    <text
                      x={x + 5}
                      y={chart.top + 12}
                      className="chart-gap-label"
                    >
                      间隔 {Math.round(elapsed / 60)} 分钟
                    </text>
                  </g>
                );
              })}
              <text
                x="4"
                y={chart.top + chart.priceHeight + 18}
                className="chart-axis-label"
              >
                {new Date(chart.bars[0].timestamp).toLocaleString("zh-CN", {
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </text>
              <text
                x={chart.width - 4}
                y={chart.top + chart.priceHeight + 18}
                textAnchor="end"
                className="chart-axis-label"
              >
                {new Date(chart.bars.at(-1)!.timestamp).toLocaleString(
                  "zh-CN",
                  {
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  },
                )}
              </text>
              <text
                x="4"
                y={chart.volumeTop + chart.volumeHeight + 20}
                className="chart-axis-label"
              >
                成交量
              </text>
            </svg>
          </div>
          {indicators && (
            <div className="market-indicator-readouts" aria-label="指标摘要">
              {(
                [
                  [
                    "MACD",
                    indicators.macd.at(-1),
                    indicators.macd_signal.at(-1),
                  ],
                  ["RSI 14", indicators.rsi.at(-1), null],
                  ["ATR 14", indicators.atr.at(-1), null],
                ] as const
              ).map(([label, value, signal]) => (
                <div key={label}>
                  <span>{label}</span>
                  <strong>
                    {value === null ? "样本不足" : Number(value).toFixed(2)}
                  </strong>
                  {signal !== null && (
                    <small>信号 {Number(signal).toFixed(2)}</small>
                  )}
                </div>
              ))}
            </div>
          )}
          {chart.gaps.length > 0 && (
            <p className="market-chart-gap-note">
              发现 {chart.gaps.length}{" "}
              处较长时间间隔，可能与休市或数据缺口有关。
              <Link href="/workspace/data#quality">查看数据质量</Link>
            </p>
          )}
        </>
      )}
      <p className="market-chart-footnote">
        K 线基于不可变 Bar 数据集绘制，时间戳统一按 UTC 存储。
      </p>
    </section>
  );
}
