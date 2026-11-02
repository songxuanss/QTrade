#!/usr/bin/env python3
"""Run every official strategy template through the real backtest pipeline.

Creates (or revises) one persisted strategy per template, freezes it as a
validated version, runs a real backtest on the frozen acceptance dataset with
the template's schema defaults, waits for the Worker and isolated Runner, then
records orders, fills and equity. No fixture prices or synthetic returns.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".tools" / "verification-local" / "strategy-authoring"
TEMPLATES = ROOT / "apps/web/src/features/strategies/templates.json"
DATASET = "4ad3c536-681b-57d3-b2f1-7fc224093106"
START = "2025-10-30T16:00:00Z"
END = "2026-10-30T16:00:00Z"
INITIAL_CASH = "1000000"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"module unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        default="",
        help="comma separated template ids; defaults to the whole library",
    )
    arguments = parser.parse_args()
    portfolio = load_module(
        "portfolio_acceptance", ROOT / "scripts/verification-strategy-portfolio.py"
    )
    templates = json.loads(TEMPLATES.read_text(encoding="utf-8"))
    if arguments.only.strip():
        wanted = {item.strip() for item in arguments.only.split(",") if item.strip()}
        unknown = wanted - {str(item["id"]) for item in templates}
        if unknown:
            raise SystemExit(f"unknown template ids: {sorted(unknown)}")
        templates = [item for item in templates if item["id"] in wanted]
    client = httpx.Client(follow_redirects=False, timeout=300)
    session = portfolio.login(client)
    api = portfolio.Api(client, session)

    existing = {
        item["name"]: item
        for item in api.request("GET", api.workspace("strategies?limit=200&include_versions=true"))
        if not item["archived"]
    }

    output: list[dict[str, Any]] = []
    failures: list[str] = []
    for template in templates:
        name = f"模板策略 · {template['name']}"
        parameters = {
            key: rule.get("default") for key, rule in template["schema"]["properties"].items()
        }
        current = existing.get(name)
        if current is None:
            created = api.request(
                "POST",
                api.workspace("strategies"),
                {
                    "name": name,
                    "description": f"官方模板：{template['summary']}",
                    "code": template["code"],
                    "parameter_schema": template["schema"],
                    "dependencies": {},
                },
            )
            strategy_id = created["strategy"]["strategy_id"]
            version = created["version"]["version"]
            origin = "created"
        else:
            revised = api.request(
                "POST",
                api.workspace(f"strategies/{current['strategy_id']}/versions"),
                {
                    "code": template["code"],
                    "parameter_schema": template["schema"],
                    "dependencies": {},
                },
            )
            strategy_id = current["strategy_id"]
            version = revised["version"]
            origin = f"revised-from-v{current['current_version']}"
        lifecycle = api.request(
            "GET", api.workspace(f"strategies/{strategy_id}/versions/{version}/lifecycle")
        )
        if lifecycle["state"] == "draft":
            lifecycle = api.request(
                "POST",
                api.workspace(f"strategies/{strategy_id}/versions/{version}/lifecycle/transitions"),
                {"state": "validated", "reason": "official template acceptance"},
            )
        if lifecycle["state"] not in {"validated", "backtested"}:
            failures.append(f"{template['id']}: unexpected state {lifecycle['state']}")
            continue

        run = api.request(
            "POST",
            api.workspace("backtests"),
            {
                "strategy_id": strategy_id,
                "strategy_version": version,
                "dataset_id": DATASET,
                "start": START,
                "end": END,
                "initial_cash": INITIAL_CASH,
                "currency": "CNY",
                "cost_model_version": portfolio.COST_MODEL,
                "parameters": parameters,
                "random_seed": 0,
                "execution_model": portfolio.EXECUTION,
                "universe_mode": "dataset",
                "rights_policy": "skip",
                "adjustment_factor_mode": "freeze",
            },
        )
        run_id = run["run_id"]
        final = portfolio.wait_for(api, run_id)
        record: dict[str, Any] = {
            "template_id": template["id"],
            "name": template["name"],
            "strategy_id": strategy_id,
            "version": version,
            "origin": origin,
            "parameters": parameters,
            "run_id": run_id,
            "status": final["status"],
        }
        if final["status"] != "completed":
            record["error"] = final.get("error")
            failures.append(f"{template['id']}: {final['status']} {final.get('error')}")
            output.append(record)
            continue
        report = api.request("GET", api.workspace(f"backtests/{run_id}/report"))
        summary = portfolio.summarize(report, run_id)
        record.update(
            {
                "orders": len(report["orders"]),
                "fills": len(report["fills"]),
                "equity_points": len(report["equity_curve"]),
                "final_equity": summary["final_equity"],
                "total_return": summary["total_return"],
                "max_drawdown": summary["max_drawdown"],
                "sharpe_ratio": summary["sharpe_ratio"],
            }
        )
        if not report["orders"] or not report["fills"] or len(report["equity_curve"]) < 2:
            failures.append(f"{template['id']}: no orders/fills/equity despite completed run")
        else:
            api.request(
                "POST",
                api.workspace(f"strategies/{strategy_id}/versions/{version}/lifecycle/transitions"),
                {"state": "backtested", "reason": "official template acceptance"},
            )
        output.append(record)

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    target = EVIDENCE / "templates-backtest.json"
    target.write_text(
        json.dumps(
            {
                "dataset_id": DATASET,
                "start": START,
                "end": END,
                "initial_cash": INITIAL_CASH,
                "templates": output,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"{'id':<28}{'orders':>7}{'fills':>7}{'points':>8}{'return':>10}{'mdd':>9}")
    for record in output:
        if record["status"] != "completed":
            print(f"{record['template_id']:<28}{'FAILED':>7} {record.get('error')}")
            continue
        print(
            f"{record['template_id']:<28}{record['orders']:>7}{record['fills']:>7}"
            f"{record['equity_points']:>8}{float(record['total_return']):>10.4f}"
            f"{float(record['max_drawdown']):>9.4f}"
        )
    if failures:
        print("\nfailures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"\n{len(output)} templates executed on {DATASET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
