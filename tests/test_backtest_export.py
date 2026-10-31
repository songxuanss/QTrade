"""CSV/HTML exports must reproduce the frozen, verified run artifacts."""

import csv
import json
from datetime import UTC, datetime
from hashlib import sha256
from io import StringIO
from pathlib import Path
from uuid import UUID

from qtrade_application.backtest_export import (
    run_csv,
    run_html,
    walk_forward_csv,
    walk_forward_html,
)
from qtrade_contracts import (
    ArtifactManifest,
    BacktestFill,
    BacktestOrder,
    BacktestPosition,
    BacktestResult,
    BacktestRun,
    BacktestWalkForwardAggregate,
    BacktestWalkForwardRun,
    BacktestWalkForwardWindowOutcome,
)

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


def load(model):
    return model.model_validate_json((FIXTURES / f"{model.__name__}.json").read_text())


def manifest_with(parameters: dict[str, object]) -> ArtifactManifest:
    base = load(ArtifactManifest)
    value = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    return base.model_copy(
        update={
            "parameters_json": value,
            "parameters_sha256": sha256(value.encode()).hexdigest(),
        }
    )


def sections(content: str) -> dict[str, list[list[str]]]:
    parsed: dict[str, list[list[str]]] = {}
    current: list[list[str]] | None = None
    for row in csv.reader(StringIO(content)):
        if row and row[0].startswith("# section: "):
            current = parsed.setdefault(row[0].removeprefix("# section: "), [])
            continue
        if current is not None and row:
            current.append(row)
    return parsed


def populated_result() -> BacktestResult:
    result = load(BacktestResult)
    point = result.equity_curve[0]
    return result.model_copy(
        update={
            "equity_curve": (
                point.model_copy(
                    update={
                        "positions": (
                            BacktestPosition(
                                instrument_id="SSE:600000",
                                quantity="100",
                                market_value="1050",
                            ),
                        )
                    }
                ),
            ),
            "orders": (
                BacktestOrder(
                    instrument_id="SSE:600000",
                    side="buy",
                    quantity="100",
                    filled_quantity="100",
                    submitted_at=point.timestamp,
                    filled_at=point.timestamp,
                    price="10",
                    status="filled",
                ),
            ),
            "fills": (
                BacktestFill(
                    instrument_id="SSE:600000",
                    side="buy",
                    filled_quantity="100",
                    filled_at=point.timestamp,
                    price="10",
                    fee="5",
                ),
            ),
        }
    )


def test_csv_export_reproduces_metrics_orders_and_fills():
    run, result, manifest = load(BacktestRun), populated_result(), load(ArtifactManifest)

    artifact = run_csv(run, result, manifest)
    content = artifact.content.decode("utf-8")
    parsed = sections(content)

    assert artifact.media_type == "text/csv; charset=utf-8"
    assert artifact.filename.startswith("qtrade-backtest-")
    assert artifact.filename.endswith(".csv")
    assert set(parsed) >= {
        "run",
        "metrics",
        "parameters",
        "equity_curve",
        "orders",
        "fills",
    }
    metrics = {row[0]: row[1] for row in parsed["metrics"][1:]}
    assert metrics["total_return"] == format(result.summary.total_return, "f")
    assert metrics["sample_count"] == str(result.summary.sample_count)
    fills = parsed["fills"][1:]
    assert len(fills) == len(result.fills)
    assert fills[0][1] == result.fills[0].instrument_id
    assert fills[0][4] == format(result.fills[0].price, "f")
    assert parsed["equity_curve"][1][5] == "SSE:600000=100"
    assert parsed["orders"][1][1] == "SSE:600000"
    assert parsed["run"][1][1] == str(run.run_id)
    assert parsed["equity_curve"][1][0] == result.equity_curve[0].timestamp.isoformat()


def test_csv_export_quotes_commas_and_newlines_safely():
    run, result = load(BacktestRun), populated_result()
    manifest = manifest_with({"note": 'a,b\n"quoted"'})

    content = run_csv(run, result, manifest).content.decode("utf-8")
    rows = list(csv.reader(StringIO(content)))
    parameter_row = next(row for row in rows if row and row[0] == "note")
    assert parameter_row[1] == json.dumps('a,b\n"quoted"', ensure_ascii=False)


def test_html_export_escapes_parameters_and_includes_key_sections():
    run, result = load(BacktestRun), populated_result()
    manifest = manifest_with({"injected": "<script>alert(1)</script>"})

    artifact = run_html(run, result, manifest)
    document = artifact.content.decode("utf-8")

    assert artifact.media_type == "text/html; charset=utf-8"
    assert artifact.filename.endswith(".html")
    assert "<script>alert(1)</script>" not in document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document
    assert "绩效指标" in document
    assert "冻结输入" in document
    assert "订单" in document
    assert "成交" in document
    assert "window.print()" in document
    assert "@media print" in document
    assert str(run.run_id) in document


def test_walk_forward_exports_include_aggregate_and_window_errors():
    from datetime import timedelta

    start = datetime(2026, 10, 1, tzinfo=UTC)
    windows = (
        BacktestWalkForwardWindowOutcome(
            schema_version=1,
            workspace_id=UUID(int=8),
            index=1,
            train_start=start,
            train_end=start + timedelta(days=1),
            validation_start=start + timedelta(days=1),
            validation_end=start + timedelta(days=2),
            train_samples=5,
            validation_samples=5,
            run_id=UUID(int=1),
            status="completed",
            total_return="0.0125",
            max_drawdown="0.02",
            sharpe_ratio="1.5",
            trade_count=2,
        ),
        BacktestWalkForwardWindowOutcome(
            schema_version=1,
            workspace_id=UUID(int=8),
            index=2,
            train_start=start + timedelta(days=1),
            train_end=start + timedelta(days=2),
            validation_start=start + timedelta(days=2),
            validation_end=start + timedelta(days=3),
            train_samples=5,
            validation_samples=5,
            run_id=UUID(int=2),
            status="failed",
            error_code="engine_error",
        ),
    )
    batch = BacktestWalkForwardRun(
        schema_version=1,
        workspace_id=UUID(int=8),
        walk_forward_id=UUID(int=9),
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
        strategy_id=UUID(int=3),
        strategy_version=1,
        dataset_id=UUID(int=4),
        instrument_id="SSE:600000",
        start=start,
        end=start + timedelta(days=3),
        initial_cash="100000",
        currency="CNY",
        cost_model_version="cost.v1",
        parameters_json='{"quantity":100}',
        execution_config_json="{}",
        random_seed=0,
        train_window_seconds=86400,
        validation_window_seconds=86400,
        step_seconds=86400,
        status="partial",
        windows=windows,
        aggregate=BacktestWalkForwardAggregate(
            schema_version=1,
            workspace_id=UUID(int=8),
            completed_windows=1,
            failed_windows=1,
            mean_validation_return="0.0125",
            median_validation_return="0.0125",
            mean_max_drawdown="0.02",
            worst_max_drawdown="0.02",
            positive_window_ratio="1",
            compounded_validation_return="0.0125",
            total_trade_count=2,
        ),
    )

    csv_content = walk_forward_csv(batch).content.decode("utf-8")
    parsed = sections(csv_content)
    aggregate = {row[0]: row[1] for row in parsed["aggregate"][1:]}
    assert aggregate["completed_windows"] == "1"
    assert aggregate["failed_windows"] == "1"
    assert aggregate["compounded_validation_return"] == "0.0125"
    window_rows = parsed["windows"][1:]
    assert window_rows[0][1] == str(UUID(int=1))
    assert window_rows[1][2] == "failed"
    assert window_rows[1][13] == "engine_error"

    document = walk_forward_html(batch).content.decode("utf-8")
    assert "窗口明细" in document
    assert "1.25%" in document
    assert "engine_error" in document
    assert "未用于参数寻优" in document
    assert "window.print()" in document
