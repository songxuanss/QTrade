# ruff: noqa: E501
"""Render immutable backtest artifacts as downloadable CSV and printable HTML."""

import csv
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from typing import Any, cast

from qtrade_contracts import (
    ArtifactManifest,
    BacktestPosition,
    BacktestResult,
    BacktestRun,
    BacktestWalkForwardRun,
)
from qtrade_domain.ports import ErrorCode, PortError

ExportFormat = str


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    content: bytes
    media_type: str
    filename: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.content, bytes)
            or not self.content
            or self.media_type not in {"text/csv; charset=utf-8", "text/html; charset=utf-8"}
            or not self.filename
            or "/" in self.filename
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid export artifact")


def parse_format(value: str) -> str:
    if value not in {"csv", "html"}:
        raise PortError(ErrorCode.INVALID_INPUT, "export format must be csv or html")
    return value


def _timestamp_slug(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%d")


def _decimal(value: object) -> str:
    return format(value, "f") if isinstance(value, Decimal) else ""


def _section(
    writer: Any, name: str, header: tuple[str, ...], rows: list[tuple[object, ...]]
) -> None:
    writer.writerow([f"# section: {name}"])
    writer.writerow(list(header))
    for row in rows:
        writer.writerow(["" if value is None else str(value) for value in row])
    writer.writerow([])


def _execution_config(manifest: ArtifactManifest) -> dict[str, object]:
    try:
        config = json.loads(manifest.execution_config_json or "{}")
    except ValueError:
        raise PortError(ErrorCode.CHECKSUM_MISMATCH, "frozen execution config is invalid") from None
    return config if isinstance(config, dict) else {}


def _parameters(manifest: ArtifactManifest) -> dict[str, object]:
    try:
        value = json.loads(manifest.parameters_json or "{}")
    except ValueError:
        raise PortError(ErrorCode.CHECKSUM_MISMATCH, "frozen parameters are invalid") from None
    return value if isinstance(value, dict) else {}


def _universe(manifest: ArtifactManifest) -> dict[str, object]:
    value = _execution_config(manifest).get("universe")
    return value if isinstance(value, dict) else {}


def _universe_memberships(manifest: ArtifactManifest) -> list[dict[str, object]]:
    memberships = _universe(manifest).get("memberships", [])
    return (
        [item for item in memberships if isinstance(item, dict)]
        if isinstance(memberships, list)
        else []
    )


def _corporate_actions(manifest: ArtifactManifest) -> list[dict[str, object]]:
    actions = _execution_config(manifest).get("corporate_actions", [])
    return [item for item in actions if isinstance(item, dict)] if isinstance(actions, list) else []


def _positions(point_positions: Sequence[BacktestPosition]) -> str:
    return ";".join(f"{item.instrument_id}={_decimal(item.quantity)}" for item in point_positions)


def _run_rows(
    run: BacktestRun, result: BacktestResult, manifest: ArtifactManifest
) -> list[tuple[object, ...]]:
    return [
        ("run_id", run.run_id),
        ("status", run.status),
        ("strategy_version_id", run.strategy_version_id),
        ("dataset_id", run.dataset_id),
        ("manifest_id", run.manifest_id),
        ("start", run.start.isoformat()),
        ("end", run.end.isoformat()),
        ("initial_cash", _decimal(run.initial_cash)),
        ("currency", run.currency),
        ("benchmark_id", run.benchmark_id),
        ("cost_model_version", run.cost_model_version),
        ("engine", result.determinism.engine),
        ("engine_name", manifest.engine_name),
        ("engine_version", manifest.engine_version),
        ("adapter_version", manifest.adapter_version),
        ("random_seed", manifest.random_seed),
        ("code_sha256", manifest.code_sha256),
        ("dataset_sha256", manifest.dataset_sha256),
    ]


def run_csv(run: BacktestRun, result: BacktestResult, manifest: ArtifactManifest) -> ExportArtifact:
    buffer = io.StringIO()
    writer: Any = csv.writer(buffer, lineterminator="\n")
    writer.writerow([f"# QTrade backtest export · {run.run_id}"])
    writer.writerow([f"# generated_at: {datetime.now(UTC).isoformat()}"])
    writer.writerow([])
    _section(writer, "run", ("field", "value"), _run_rows(run, result, manifest))
    summary_rows: list[tuple[object, ...]] = [
        ("initial_cash", _decimal(result.summary.initial_cash)),
        ("final_equity", _decimal(result.summary.final_equity)),
        ("total_return", _decimal(result.summary.total_return)),
        ("ending_drawdown", _decimal(result.summary.ending_drawdown)),
        ("sample_count", result.summary.sample_count),
    ]
    if result.metrics is not None:
        metrics = result.metrics
        summary_rows.extend(
            (name, _decimal(getattr(metrics, name)))
            for name in (
                "max_drawdown",
                "annualized_return",
                "annualized_volatility",
                "sharpe_ratio",
                "sortino_ratio",
                "benchmark_return",
                "calmar_ratio",
                "win_rate",
                "profit_factor",
                "turnover",
                "average_exposure",
                "max_exposure",
                "trade_count",
                "winning_trades",
                "losing_trades",
            )
        )
    _section(writer, "metrics", ("field", "value"), summary_rows)
    _section(
        writer,
        "parameters",
        ("name", "value"),
        [
            (name, json.dumps(value, ensure_ascii=False))
            for name, value in _parameters(manifest).items()
        ],
    )
    execution = _execution_config(manifest).get("execution_model", {})
    _section(
        writer,
        "execution_model",
        ("name", "value"),
        [(name, json.dumps(value, ensure_ascii=False)) for name, value in execution.items()]
        if isinstance(execution, dict)
        else [],
    )
    universe = _universe(manifest)
    _section(
        writer,
        "universe",
        ("field", "value"),
        [
            ("mode", universe.get("mode")),
            ("instruments", universe.get("instruments")),
            ("full_coverage", universe.get("full_coverage")),
            ("partial_coverage", universe.get("partial_coverage")),
            ("unknown", universe.get("unknown")),
            ("disclosure", universe.get("disclosure")),
        ],
    )
    _section(
        writer,
        "universe_memberships",
        (
            "instrument_id",
            "effective_from",
            "effective_to",
            "source",
            "evidence",
            "content_version",
        ),
        [
            (
                item.get("instrument_id"),
                item.get("effective_from"),
                item.get("effective_to"),
                item.get("source"),
                item.get("evidence"),
                item.get("content_version"),
            )
            for item in _universe_memberships(manifest)
        ],
    )
    _section(
        writer,
        "corporate_actions",
        (
            "ex_date",
            "action",
            "basis_shares",
            "cash",
            "cash_currency",
            "bonus_shares",
            "transfer_shares",
            "split_ratio",
            "content_version",
        ),
        [
            (
                action.get("ex_date"),
                action.get("action"),
                action.get("basis_shares"),
                action.get("cash"),
                action.get("cash_currency"),
                action.get("bonus_shares"),
                action.get("transfer_shares"),
                action.get("split_ratio"),
                action.get("content_version"),
            )
            for action in _corporate_actions(manifest)
        ],
    )
    _section(
        writer,
        "equity_curve",
        ("timestamp", "cash", "equity", "drawdown", "benchmark_equity", "positions"),
        [
            (
                point.timestamp.isoformat(),
                _decimal(point.cash),
                _decimal(point.equity),
                _decimal(point.drawdown),
                _decimal(point.benchmark_equity),
                _positions(point.positions),
            )
            for point in result.equity_curve
        ],
    )
    _section(
        writer,
        "orders",
        (
            "submitted_at",
            "instrument_id",
            "side",
            "quantity",
            "filled_quantity",
            "price",
            "status",
            "reason",
        ),
        [
            (
                order.submitted_at.isoformat(),
                order.instrument_id,
                order.side,
                _decimal(order.quantity),
                _decimal(order.filled_quantity),
                _decimal(order.price) if order.price is not None else "",
                order.status,
                order.reason or "signal",
            )
            for order in result.orders
        ],
    )
    _section(
        writer,
        "fills",
        ("filled_at", "instrument_id", "side", "filled_quantity", "price", "fee"),
        [
            (
                fill.filled_at.isoformat(),
                fill.instrument_id,
                fill.side,
                _decimal(fill.filled_quantity),
                _decimal(fill.price),
                _decimal(fill.fee),
            )
            for fill in result.fills
        ],
    )
    return ExportArtifact(
        buffer.getvalue().encode("utf-8"),
        "text/csv; charset=utf-8",
        f"qtrade-backtest-{str(run.run_id)[:8]}-{_timestamp_slug(run.start)}.csv",
    )


HTML_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px; font: 14px/1.5 -apple-system, "Segoe UI", "Noto Sans SC", sans-serif;
  color: #16202b; background: #fff; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 28px 0 8px; }
p.meta { margin: 0; color: #5b6b7c; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-top: 16px; }
.card { border: 1px solid #d9e0e7; border-radius: 8px; padding: 10px 12px; }
.card span { display: block; color: #5b6b7c; font-size: 11px; letter-spacing: .04em; }
.card strong { font-size: 18px; font-variant-numeric: tabular-nums; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { border-bottom: 1px solid #e4e9ee; padding: 6px 8px; text-align: left; }
th { background: #f4f7f9; font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.note { color: #5b6b7c; font-size: 12px; }
.chart { width: 100%; height: 220px; border: 1px solid #e4e9ee; border-radius: 8px; margin-top: 12px; }
.bar { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
button { border: 1px solid #1f7a5c; background: #1f7a5c; color: #fff; border-radius: 6px;
  padding: 8px 14px; font: inherit; cursor: pointer; }
@media print {
  body { padding: 0; }
  .actions { display: none; }
  h2 { break-after: avoid; }
  table { break-inside: auto; }
  tr { break-inside: avoid; }
}
"""


def _percent(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(cast(Decimal, value)) * 100:.2f}%"


def _ratio(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(cast(Decimal, value)):.2f}"


def _money(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(cast(Decimal, value)):,.2f}"


def _table(headers: tuple[str, ...], rows: Sequence[Sequence[object]], numeric: set[int]) -> str:
    head = "".join(f"<th>{escape(item)}</th>" for item in headers)
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="{"num" if index in numeric else ""}">{escape(str(value))}</td>'
            for index, value in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body or '<tr><td colspan="' + str(len(headers)) + '">无记录</td></tr>'}</tbody></table>"


def _sparkline(values: list[float], width: int = 960, height: int = 220) -> str:
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = high - low or 1.0
    points = " ".join(
        f"{20 + index * (width - 40) / (len(values) - 1):.2f},"
        f"{height - 20 - (value - low) / span * (height - 40):.2f}"
        for index, value in enumerate(values)
    )
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="权益曲线">'
        f'<polyline fill="none" stroke="#1f7a5c" stroke-width="1.6" points="{points}" />'
        f'<text x="20" y="{height - 4}" font-size="11" fill="#5b6b7c">{escape(f"{low:,.2f}")}</text>'
        f'<text x="{width - 20}" y="14" font-size="11" fill="#5b6b7c" text-anchor="end">'
        f"{escape(f'{high:,.2f}')}</text></svg>"
    )


def run_html(
    run: BacktestRun,
    result: BacktestResult,
    manifest: ArtifactManifest,
    *,
    strategy_label: str | None = None,
    dataset_label: str | None = None,
) -> ExportArtifact:
    metrics = result.metrics
    cards = (
        ("累计收益", _percent(result.summary.total_return)),
        ("最大回撤", _percent(metrics.max_drawdown if metrics else result.summary.ending_drawdown)),
        ("年化收益", _percent(metrics.annualized_return if metrics else None)),
        ("Sharpe", _ratio(metrics.sharpe_ratio if metrics else None)),
        ("交易笔数", str(metrics.trade_count) if metrics else "0"),
        ("期末权益", _money(result.summary.final_equity)),
    )
    summary_rows = [
        ("初始资金", _money(result.summary.initial_cash)),
        ("期末权益", _money(result.summary.final_equity)),
        ("累计收益", _percent(result.summary.total_return)),
        ("样本数", str(result.summary.sample_count)),
    ]
    if metrics is not None:
        summary_rows.extend(
            [
                ("最大回撤", _percent(metrics.max_drawdown)),
                ("年化波动", _percent(metrics.annualized_volatility)),
                ("Sortino", _ratio(metrics.sortino_ratio)),
                ("Calmar", _ratio(metrics.calmar_ratio)),
                ("胜率", _percent(metrics.win_rate)),
                ("盈亏比", _ratio(metrics.profit_factor)),
                ("换手率", _percent(metrics.turnover)),
                (
                    "平均/最大暴露",
                    f"{_percent(metrics.average_exposure)} / {_percent(metrics.max_exposure)}",
                ),
                ("盈利/亏损交易", f"{metrics.winning_trades} / {metrics.losing_trades}"),
            ]
        )
    frozen_rows = [
        ("策略", strategy_label or run.strategy_version_id),
        ("数据集", dataset_label or run.dataset_id),
        ("区间", f"{run.start.isoformat()} → {run.end.isoformat()}"),
        ("币种", run.currency),
        ("基准", run.benchmark_id or "未设置"),
        ("成本模型", run.cost_model_version),
        ("引擎", f"{manifest.engine_name} {manifest.engine_version}"),
        ("适配器", manifest.adapter_version),
        ("随机种子", str(manifest.random_seed)),
        ("代码 SHA256", manifest.code_sha256),
        ("数据 SHA256", manifest.dataset_sha256),
    ]
    parameter_rows = [
        (str(name), json.dumps(value, ensure_ascii=False))
        for name, value in _parameters(manifest).items()
    ]
    execution = _execution_config(manifest).get("execution_model", {})
    execution_rows = (
        [(str(name), json.dumps(value, ensure_ascii=False)) for name, value in execution.items()]
        if isinstance(execution, dict)
        else []
    )
    action_rows = [
        (
            str(action.get("ex_date") or ""),
            str(action.get("action") or ""),
            str(action.get("basis_shares") or ""),
            str(action.get("cash") or ""),
            str(action.get("cash_currency") or ""),
            str(action.get("bonus_shares") or ""),
            str(action.get("transfer_shares") or ""),
            str(action.get("split_ratio") or ""),
            str(action.get("content_version") or ""),
        )
        for action in _corporate_actions(manifest)
    ]
    final_positions = result.equity_curve[-1].positions
    position_rows = [
        (item.instrument_id, _decimal(item.quantity), _money(item.market_value))
        for item in final_positions
    ]
    order_rows = [
        (
            order.submitted_at.isoformat(),
            order.instrument_id,
            order.side,
            _decimal(order.quantity),
            _decimal(order.filled_quantity),
            _decimal(order.price) if order.price is not None else "—",
            order.status,
            "成员区间结束" if order.reason == "membership_end" else "策略信号",
        )
        for order in result.orders
    ]
    fill_rows = [
        (
            fill.filled_at.isoformat(),
            fill.instrument_id,
            fill.side,
            _decimal(fill.filled_quantity),
            _decimal(fill.price),
            _decimal(fill.fee),
        )
        for fill in result.fills
    ]
    equity = [float(point.equity) for point in result.equity_curve]
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>QTrade 回测报告 {escape(str(run.run_id)[:8])}</title>
<style>{HTML_STYLE}</style>
</head>
<body>
<div class="bar">
  <div>
    <h1>QTrade 回测报告</h1>
    <p class="meta">Run {escape(str(run.run_id))} · {
        escape(str(strategy_label or run.strategy_version_id))
    } · {escape(str(dataset_label or run.dataset_id))}</p>
  </div>
  <div class="actions"><button type="button" onclick="window.print()">打印 / 另存为 PDF</button></div>
</div>
<div class="grid">
{
        "".join(
            f'<div class="card"><span>{escape(label)}</span><strong>{escape(value)}</strong></div>'
            for label, value in cards
        )
    }
</div>
{_sparkline(equity)}
<h2>绩效指标</h2>
{_table(("指标", "数值"), summary_rows, set())}
<h2>冻结输入</h2>
{_table(("项目", "值"), frozen_rows, set())}
<h2>策略参数</h2>
{_table(("参数", "值"), parameter_rows, set())}
<h2>执行假设</h2>
{_table(("参数", "值"), execution_rows, set())}
<h2>成员区间与幸存者偏差</h2>
<p class="note">{escape(str(_universe(manifest).get("disclosure") or "未提供成员区间披露。"))}</p>
{
        _table(
            ("项目", "值"),
            [
                ("校验模式", str(_universe(manifest).get("mode") or "dataset")),
                ("数据集标的", str(_universe(manifest).get("instruments") or 0)),
                ("全程覆盖", str(_universe(manifest).get("full_coverage") or 0)),
                ("区间不完整", str(_universe(manifest).get("partial_coverage") or 0)),
                ("未知区间", str(_universe(manifest).get("unknown") or 0)),
            ],
            set(),
        )
    }
{
        _table(
            ("标的", "生效起", "生效止", "来源", "证据", "区间版本"),
            [
                (
                    str(item.get("instrument_id") or ""),
                    str(item.get("effective_from") or ""),
                    str(item.get("effective_to") or ""),
                    str(item.get("source") or ""),
                    str(item.get("evidence") or ""),
                    str(item.get("content_version") or ""),
                )
                for item in _universe_memberships(manifest)
            ],
            set(),
        )
    }
<h2>已冻结公司行为</h2>
{
        _table(
            (
                "除权日",
                "类型",
                "基准股数",
                "每股金额",
                "币种",
                "送股",
                "转增",
                "拆并比例",
                "事件版本",
            ),
            action_rows,
            set(),
        )
    }
<h2>期末持仓</h2>
{_table(("标的", "数量", "市值"), position_rows, {1, 2})}
<h2>订单 ({len(order_rows)})</h2>
{
        _table(
            ("提交时间", "标的", "方向", "数量", "成交数量", "价格", "状态", "原因"),
            order_rows,
            {3, 4, 5},
        )
    }
<h2>成交 ({len(fill_rows)})</h2>
{_table(("成交时间", "标的", "方向", "数量", "价格", "费用"), fill_rows, {3, 4, 5})}
<p class="note">本文件由 QTrade 从已校验的不可变结果、manifest 与运行记录生成，生成时间 {
        escape(datetime.now(UTC).isoformat())
    }。
用于研究复核，不构成实盘可用性或收益承诺。</p>
</body>
</html>
"""
    return ExportArtifact(
        document.encode("utf-8"),
        "text/html; charset=utf-8",
        f"qtrade-backtest-{str(run.run_id)[:8]}-{_timestamp_slug(run.start)}.html",
    )


def walk_forward_csv(batch: BacktestWalkForwardRun) -> ExportArtifact:
    buffer = io.StringIO()
    writer: Any = csv.writer(buffer, lineterminator="\n")
    writer.writerow([f"# QTrade walk-forward export · {batch.walk_forward_id}"])
    writer.writerow([f"# generated_at: {datetime.now(UTC).isoformat()}"])
    writer.writerow([])
    _section(
        writer,
        "walk_forward",
        ("field", "value"),
        [
            ("walk_forward_id", batch.walk_forward_id),
            ("status", batch.status),
            ("strategy_id", batch.strategy_id),
            ("strategy_version", batch.strategy_version),
            ("dataset_id", batch.dataset_id),
            ("instrument_id", batch.instrument_id),
            ("start", batch.start.isoformat()),
            ("end", batch.end.isoformat()),
            ("initial_cash", _decimal(batch.initial_cash)),
            ("currency", batch.currency),
            ("benchmark_id", batch.benchmark_id),
            ("cost_model_version", batch.cost_model_version),
            ("random_seed", batch.random_seed),
            ("train_window_seconds", batch.train_window_seconds),
            ("validation_window_seconds", batch.validation_window_seconds),
            ("step_seconds", batch.step_seconds),
            ("parameters_json", batch.parameters_json),
        ],
    )
    aggregate = batch.aggregate
    _section(
        writer,
        "aggregate",
        ("field", "value"),
        [
            ("completed_windows", aggregate.completed_windows if aggregate else 0),
            ("failed_windows", aggregate.failed_windows if aggregate else 0),
            (
                "mean_validation_return",
                _decimal(aggregate.mean_validation_return) if aggregate else "",
            ),
            (
                "median_validation_return",
                _decimal(aggregate.median_validation_return) if aggregate else "",
            ),
            (
                "compounded_validation_return",
                _decimal(aggregate.compounded_validation_return) if aggregate else "",
            ),
            (
                "positive_window_ratio",
                _decimal(aggregate.positive_window_ratio) if aggregate else "",
            ),
            ("mean_max_drawdown", _decimal(aggregate.mean_max_drawdown) if aggregate else ""),
            ("worst_max_drawdown", _decimal(aggregate.worst_max_drawdown) if aggregate else ""),
            ("total_trade_count", aggregate.total_trade_count if aggregate else 0),
        ],
    )
    _section(
        writer,
        "windows",
        (
            "index",
            "run_id",
            "status",
            "train_start",
            "train_end",
            "validation_start",
            "validation_end",
            "train_samples",
            "validation_samples",
            "total_return",
            "max_drawdown",
            "sharpe_ratio",
            "trade_count",
            "error_code",
        ),
        [
            (
                window.index,
                window.run_id,
                window.status,
                window.train_start.isoformat(),
                window.train_end.isoformat(),
                window.validation_start.isoformat(),
                window.validation_end.isoformat(),
                window.train_samples,
                window.validation_samples,
                _decimal(window.total_return) if window.total_return is not None else "",
                _decimal(window.max_drawdown) if window.max_drawdown is not None else "",
                _decimal(window.sharpe_ratio) if window.sharpe_ratio is not None else "",
                window.trade_count if window.trade_count is not None else "",
                window.error_code,
            )
            for window in batch.windows
        ],
    )
    return ExportArtifact(
        buffer.getvalue().encode("utf-8"),
        "text/csv; charset=utf-8",
        f"qtrade-walk-forward-{str(batch.walk_forward_id)[:8]}-{_timestamp_slug(batch.created_at)}.csv",
    )


def walk_forward_html(batch: BacktestWalkForwardRun) -> ExportArtifact:
    aggregate = batch.aggregate
    cards = (
        ("批次状态", batch.status),
        ("完成窗口", str(aggregate.completed_windows) if aggregate else "0"),
        ("失败窗口", str(aggregate.failed_windows) if aggregate else "0"),
        ("验证均值收益", _percent(aggregate.mean_validation_return) if aggregate else "—"),
        ("窗口复利收益", _percent(aggregate.compounded_validation_return) if aggregate else "—"),
        ("正收益窗口占比", _percent(aggregate.positive_window_ratio) if aggregate else "—"),
    )
    window_rows = [
        (
            str(window.index),
            f"{window.validation_start.isoformat()} → {window.validation_end.isoformat()}",
            str(window.validation_samples),
            window.status,
            _percent(window.total_return),
            _percent(window.max_drawdown),
            str(window.trade_count) if window.trade_count is not None else "—",
            str(window.run_id),
            window.error_code or "",
        )
        for window in batch.windows
    ]
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>QTrade Walk-forward {escape(str(batch.walk_forward_id)[:8])}</title>
<style>{HTML_STYLE}</style>
</head>
<body>
<div class="bar">
  <div>
    <h1>QTrade Walk-forward 报告</h1>
    <p class="meta">{escape(batch.instrument_id)} · {escape(str(batch.start))} → {
        escape(str(batch.end))
    } · 批次 {escape(str(batch.walk_forward_id))}</p>
  </div>
  <div class="actions"><button type="button" onclick="window.print()">打印 / 另存为 PDF</button></div>
</div>
<div class="grid">
{
        "".join(
            f'<div class="card"><span>{escape(label)}</span><strong>{escape(value)}</strong></div>'
            for label, value in cards
        )
    }
</div>
<h2>窗口明细</h2>
{
        _table(
            ("窗口", "验证区间", "样本", "状态", "收益", "最大回撤", "成交", "运行", "错误"),
            window_rows,
            {2, 4, 5, 6},
        )
    }
<h2>冻结输入</h2>
{
        _table(
            ("项目", "值"),
            [
                ("策略版本", f"{batch.strategy_id} v{batch.strategy_version}"),
                ("数据集", batch.dataset_id),
                ("初始资金", _money(batch.initial_cash)),
                ("币种", batch.currency),
                ("成本模型", batch.cost_model_version),
                ("随机种子", str(batch.random_seed)),
                (
                    "训练/验证/步长(秒)",
                    f"{batch.train_window_seconds} / {batch.validation_window_seconds} / {batch.step_seconds}",
                ),
                ("策略参数", batch.parameters_json),
            ],
            set(),
        )
    }
<p class="note">训练区间仅用于切分核对，未用于参数寻优；每个窗口独立使用初始资金，不做跨窗口资金复利。
聚合只统计已完成窗口，失败或取消窗口单独计数。生成时间 {escape(datetime.now(UTC).isoformat())}。</p>
</body>
</html>
"""
    return ExportArtifact(
        document.encode("utf-8"),
        "text/html; charset=utf-8",
        f"qtrade-walk-forward-{str(batch.walk_forward_id)[:8]}-{_timestamp_slug(batch.created_at)}.html",
    )
