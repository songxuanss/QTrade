#!/usr/bin/env python3
"""Run the strategy created from a Web template through the real backtest pipeline.

Reads the strategy id recorded by ``apps/web/tests/verification-strategy-authoring.mjs``,
then creates a backtest on the frozen acceptance dataset, waits for the Worker
and isolated Runner to finish, and asserts nonempty orders, fills and equity.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]


def load_portfolio_module() -> Any:
    path = ROOT / "scripts" / "verification-strategy-portfolio.py"
    spec = importlib.util.spec_from_file_location("portfolio_acceptance", path)
    if spec is None or spec.loader is None:
        raise SystemExit("portfolio acceptance module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_portfolio_module()
    evidence = ROOT / ".tools" / "verification-local" / "strategy-authoring"
    report = json.loads((evidence / "report.json").read_text(encoding="utf-8"))
    strategy_id = next(
        step["strategyId"] for step in report["steps"] if step["name"] == "保存模板策略"
    )
    templates = {
        item["id"]: item
        for item in json.loads(
            (ROOT / "apps/web/src/features/strategies/templates.json").read_text(encoding="utf-8")
        )
    }
    template = templates["momentum-rank"]
    dataset = os.environ.get("QTRADE_TEMPLATE_DATASET", "4ad3c536-681b-57d3-b2f1-7fc224093106")

    client = httpx.Client(follow_redirects=False, timeout=300)
    session = os.environ.get("QTRADE_ACCEPTANCE_SESSION") or ""
    if session:
        probe = client.get(
            f"{module.API}/auth/session", headers={"Cookie": f"qtrade-session={session}"}
        )
        if probe.status_code != 200:
            session = ""
    if not session:
        session = module.login(client)
    api = module.Api(client, session)

    detail = api.request("GET", api.workspace("strategies?limit=200&include_versions=true"))
    entry = next(item for item in detail if item["strategy_id"] == strategy_id)
    version = entry["current_version"]
    stored = next(item for item in entry["strategy_versions"] if item["version"] == version)
    parameters = {
        name: rule.get("default") for name, rule in stored["parameter_schema"]["properties"].items()
    }
    if parameters.get("top_n") != 3:
        raise SystemExit(f"edited schema default was not persisted: {parameters!r}")
    lifecycle = api.request(
        "GET", api.workspace(f"strategies/{strategy_id}/versions/{version}/lifecycle")
    )
    if lifecycle["state"] == "draft":
        api.request(
            "POST",
            api.workspace(f"strategies/{strategy_id}/versions/{version}/lifecycle/transitions"),
            {"state": "validated", "reason": "template authoring acceptance"},
        )
    elif lifecycle["state"] != "validated":
        raise SystemExit(f"unexpected lifecycle state {lifecycle['state']}")

    run = api.request(
        "POST",
        api.workspace("backtests"),
        {
            "strategy_id": strategy_id,
            "strategy_version": version,
            "dataset_id": dataset,
            "start": "2025-10-30T16:00:00Z",
            "end": "2026-10-30T16:00:00Z",
            "initial_cash": "1000000",
            "currency": "CNY",
            "cost_model_version": module.COST_MODEL,
            "parameters": parameters,
            "random_seed": 0,
            "execution_model": module.EXECUTION,
            "universe_mode": "dataset",
            "rights_policy": "skip",
            "adjustment_factor_mode": "freeze",
        },
    )
    run_id = run["run_id"]
    final = module.wait_for(api, run_id)
    if final["status"] != "completed":
        raise SystemExit(f"template backtest failed: {final.get('error') or final['status']}")
    result = api.request("GET", api.workspace(f"backtests/{run_id}/report"))
    summary = module.summarize(result, run_id)
    if not result["orders"] or not result["fills"] or len(result["equity_curve"]) < 2:
        raise SystemExit("template backtest produced no orders, fills or equity curve")
    output = {
        "template_id": template["id"],
        "strategy_id": strategy_id,
        "strategy_version": version,
        "dataset_id": dataset,
        "parameters": parameters,
        "run_id": run_id,
        "orders": len(result["orders"]),
        "fills": len(result["fills"]),
        "equity_points": len(result["equity_curve"]),
        "summary": summary,
    }
    target = evidence / "template-backtest.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
