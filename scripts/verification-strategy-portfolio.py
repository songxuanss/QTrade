#!/usr/bin/env python3
"""End-to-end acceptance: pool selection -> six real strategies -> comparison.

Runs against the deployed local stack through the authenticated HTTP API.  The
composed dataset, strategies, lifecycle states and backtest runs are persisted,
so the evidence can be re-read from the UI and the database after the script
exits.  No fixture data or synthetic returns are used.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / ".tools" / "verification-local" / "review"
REVIEW.mkdir(parents=True, exist_ok=True)
API = "http://127.0.0.1:3122"
WEB = "http://127.0.0.1:3120"
WORKSPACE = "3c6984b4-5cbf-470c-8d1a-56d1f5b51020"
SNAPSHOT = "bbe141c5-ed97-4224-b76e-efadcd553ec2"
DATASET_START = "2025-10-30T16:00:00Z"
DATASET_END = "2026-10-30T16:00:00Z"
INITIAL_CASH = "1000000"
COST_MODEL = "cost.v1"
EXECUTION = {
    "lot_size": 100,
    "fee_bps": "3",
    "minimum_fee": "5",
    "sell_tax_bps": "5",
    "slippage_bps": "5",
    "impact_bps": "0",
    "tick_size": "0.01",
    "settlement_days": 1,
    "max_volume_fraction": "0.1",
    "daily_price_limit_fraction": "0.1",
    "tick_aggregation_seconds": 60,
    "timezone_name": "Asia/Shanghai",
    "periods_per_year": 252,
}


def load_catalog() -> tuple[dict[str, Any], ...]:
    path = ROOT / "scripts" / "acceptance-strategy-catalog.py"
    spec = importlib.util.spec_from_file_location("acceptance_strategy_catalog", path)
    if spec is None or spec.loader is None:
        raise SystemExit("strategy catalog unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.STRATEGIES


def _follow(client: httpx.Client, response: httpx.Response) -> httpx.Response:
    location = response.headers.get("location", "")
    target = str(response.url.join(location))
    return client.get(target)


def login(client: httpx.Client) -> str:
    credentials = (ROOT / ".tools" / "verification-local" / "LOGIN.txt").read_text(encoding="utf-8")
    email = re.search(r"邮箱[：:]\s*(\S+)", credentials)
    password = re.search(r"密码[：:]\s*(\S+)", credentials)
    if email is None or password is None:
        raise SystemExit("local login instructions unavailable")
    response = client.get(f"{WEB}/auth/login")
    for _ in range(6):
        if response.status_code not in {301, 302, 303, 307, 308}:
            break
        response = _follow(client, response)
    html = response.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', html)
    txn = re.search(r'name="txn" value="([^"]+)"', html)
    if csrf is None or txn is None:
        raise SystemExit("SSO login form unavailable")
    response = client.post(
        f"{WEB.rsplit(':', 1)[0]}:3121/auth/login/password",
        data={
            "csrf_token": csrf.group(1),
            "txn": txn.group(1),
            "next": "",
            "email": email.group(1),
            "password": password.group(1),
        },
    )
    for _ in range(6):
        if response.status_code not in {301, 302, 303, 307, 308}:
            break
        response = _follow(client, response)
    consent = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    consent_txn = re.search(r'name="txn" value="([^"]+)"', response.text)
    if consent is None or consent_txn is None:
        raise SystemExit("SSO consent page unavailable")
    response = client.post(
        f"{WEB.rsplit(':', 1)[0]}:3121/auth/consent",
        data={
            "csrf_token": consent.group(1),
            "txn": consent_txn.group(1),
            "decision": "approve",
        },
    )
    for _ in range(8):
        if response.status_code not in {301, 302, 303, 307, 308}:
            break
        response = _follow(client, response)
    cookie = client.cookies.get("qtrade-session")
    if not cookie:
        raise SystemExit("login did not produce a session")
    return cookie


class Api:
    def __init__(self, client: httpx.Client, session: str):
        self.client = client
        self.session = session
        state = self.request("GET", "/auth/session")
        self.csrf = state["csrf_token"]

    def request(self, method: str, path: str, body: Any | None = None) -> Any:
        headers = {"Cookie": f"qtrade-session={self.session}"}
        if method != "GET":
            headers["X-CSRF-Token"] = self.csrf
        response = self.client.request(
            method,
            f"{API}{path}",
            headers=headers,
            json=body,
            timeout=300,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {response.status_code} {response.text[:400]}")
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return response.text

    def workspace(self, path: str) -> str:
        return f"/api/v1/workspaces/{WORKSPACE}/{path}"


def wait_for(api: Api, run_id: str, timeout: float = 1_200) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = api.request("GET", api.workspace(f"backtests/{run_id}"))
        if last["status"] in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(2)
    raise RuntimeError(f"backtest {run_id} did not finish: {last.get('status')}")


def summarize(report: dict[str, Any], run_id: str) -> dict[str, Any]:
    metrics = report["metrics"] or {}
    equity = report["equity_curve"]
    first = float(equity[0]["equity"])
    last = float(equity[-1]["equity"])
    return {
        "run_id": run_id,
        "dataset_id": report["dataset_id"],
        "strategy_version_id": report.get("strategy_version_id"),
        "start": equity[0]["timestamp"],
        "end": equity[-1]["timestamp"],
        "initial_equity": first,
        "final_equity": last,
        "total_return": report["summary"].get("total_return", last / first - 1),
        "max_drawdown": metrics.get("max_drawdown"),
        "annualized_return": metrics.get("annualized_return"),
        "annualized_volatility": metrics.get("annualized_volatility"),
        "sharpe_ratio": metrics.get("sharpe_ratio"),
        "sortino_ratio": metrics.get("sortino_ratio"),
        "calmar_ratio": metrics.get("calmar_ratio"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "turnover": metrics.get("turnover"),
        "average_exposure": metrics.get("average_exposure"),
        "max_exposure": metrics.get("max_exposure"),
        "trade_count": metrics.get("trade_count"),
        "winning_trades": metrics.get("winning_trades"),
        "losing_trades": metrics.get("losing_trades"),
        "order_count": len(report["orders"]),
        "fill_count": len(report["fills"]),
        "equity_points": len(equity),
    }


def main() -> int:
    catalog = load_catalog()
    client = httpx.Client(follow_redirects=False, timeout=300)
    session = os.environ.get("QTRADE_ACCEPTANCE_SESSION") or ""
    if session:
        probe = client.get(f"{API}/auth/session", headers={"Cookie": f"qtrade-session={session}"})
        if probe.status_code != 200:
            session = ""
    if not session:
        session = login(client)
    api = Api(client, session)
    output: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "workspace_id": WORKSPACE,
        "snapshot_id": SNAPSHOT,
        "strategies": [],
        "runs": [],
        "limitations": [],
    }

    validation = [
        api.request(
            "POST",
            api.workspace("strategies/static-validation"),
            {"code": item["code"], "dependencies": {}},
        )
        for item in catalog
    ]
    for item, result in zip(catalog, validation, strict=True):
        if not result["passed"]:
            raise SystemExit(f"static validation failed for {item['key']}: {result['diagnostics']}")
    output["static_validation"] = "passed"

    composition = api.request(
        "POST",
        api.workspace("data/datasets/compose"),
        {
            "snapshot_id": SNAPSHOT,
            "start": DATASET_START,
            "end": DATASET_END,
            "selection_size": 20,
            "granularity": "1d",
            "transformation_version": "mairui.unadjusted-bars.v1",
        },
    )
    dataset_id = composition["dataset"]["dataset_id"]
    output["composition"] = {
        "dataset_id": dataset_id,
        "instrument_count": len(composition["dataset"]["instrument_ids"]),
        "instruments": composition["dataset"]["instrument_ids"],
        "selected": composition["selected"],
        "excluded": composition["excluded"],
        "start": composition["dataset"]["start"],
        "end": composition["dataset"]["end"],
    }
    quality = api.request("GET", api.workspace(f"data/datasets/{dataset_id}/quality"))
    output["composition"]["quality"] = {
        "bar_count": quality.get("bar_count"),
        "quality_counts": quality.get("quality_counts"),
        "checks": quality.get("checks"),
        "coverage": quality.get("coverage"),
        "raw_record_count": quality.get("raw_record_count"),
    }

    existing = {
        item["name"]: item
        for item in api.request("GET", api.workspace("strategies?limit=200"))
        if not item["archived"]
    }
    for item in catalog:
        current = existing.get(item["name"])
        if current is None:
            created = api.request(
                "POST",
                api.workspace("strategies"),
                {
                    "name": item["name"],
                    "description": item["description"],
                    "code": item["code"],
                    "parameter_schema": item["parameter_schema"],
                    "dependencies": {},
                },
            )
            strategy_id = created["strategy"]["strategy_id"]
            version = created["version"]["version"]
            origin = "created"
        else:
            # Re-running acceptance revises the same named strategy instead of
            # leaving a pile of duplicates in the workspace.
            strategy_id = current["strategy_id"]
            revised = api.request(
                "POST",
                api.workspace(f"strategies/{strategy_id}/versions"),
                {
                    "code": item["code"],
                    "parameter_schema": item["parameter_schema"],
                    "dependencies": {},
                },
            )
            version = revised["version"]
            origin = f"revised-from-v{current['current_version']}"
        lifecycle = api.request(
            "POST",
            api.workspace(f"strategies/{strategy_id}/versions/{version}/lifecycle/transitions"),
            {"state": "validated", "reason": "portfolio acceptance"},
        )
        output["strategies"].append(
            {
                "key": item["key"],
                "name": item["name"],
                "strategy_id": strategy_id,
                "version": version,
                "origin": origin,
                "state": lifecycle["state"],
                "parameters": item["parameters"],
            }
        )

    for item, strategy in zip(catalog, output["strategies"], strict=True):
        run = api.request(
            "POST",
            api.workspace("backtests"),
            {
                "strategy_id": strategy["strategy_id"],
                "strategy_version": strategy["version"],
                "dataset_id": dataset_id,
                "start": composition["dataset"]["start"],
                "end": composition["dataset"]["end"],
                "initial_cash": INITIAL_CASH,
                "currency": "CNY",
                "cost_model_version": COST_MODEL,
                "parameters": item["parameters"],
                "random_seed": 0,
                "execution_model": EXECUTION,
                "universe_mode": "dataset",
                "rights_policy": "skip",
                "adjustment_factor_mode": "freeze",
            },
        )
        run_id = run["run_id"]
        final = wait_for(api, run_id)
        if final["status"] != "completed":
            output["runs"].append({"key": item["key"], "run_id": run_id, "status": final["status"]})
            continue
        report = api.request("GET", api.workspace(f"backtests/{run_id}/report"))
        summary = summarize(report, run_id)
        summary["key"] = item["key"]
        summary["name"] = item["name"]
        summary["status"] = final["status"]
        lifecycle = api.request(
            "POST",
            api.workspace(
                f"strategies/{strategy['strategy_id']}/versions/"
                f"{strategy['version']}/lifecycle/transitions"
            ),
            {"state": "backtested", "reason": f"portfolio acceptance run {run_id}"},
        )
        summary["lifecycle_state"] = lifecycle["state"]
        summary["qualification_run_id"] = run_id
        csv = api.request("GET", api.workspace(f"backtests/{run_id}/export?format=csv"))
        html = api.request("GET", api.workspace(f"backtests/{run_id}/export?format=html"))
        summary["csv_bytes"] = len(csv.encode())
        summary["html_bytes"] = len(html.encode())
        (REVIEW / f"portfolio-{item['key']}.csv").write_text(csv, encoding="utf-8")
        (REVIEW / f"portfolio-{item['key']}.html").write_text(html, encoding="utf-8")
        output["runs"].append(summary)

    completed = [item for item in output["runs"] if item.get("status") == "completed"]
    if len(completed) >= 2:
        query = "&".join(f"run_id={item['run_id']}" for item in completed)
        comparison = api.request("GET", api.workspace(f"backtests/compare?{query}"))
        names = {item["run_id"]: (item["key"], item["name"]) for item in completed}
        output["comparison"] = {
            "comparable": comparison["comparable"],
            "warnings": comparison["warnings"],
            "input_differences": comparison["input_differences"],
            "runs": [
                {
                    "run_id": run["run_id"],
                    "key": names.get(run["run_id"], ("", ""))[0],
                    "name": names.get(run["run_id"], ("", ""))[1],
                    "metrics": run["result"]["metrics"],
                    "summary": run["result"]["summary"],
                }
                for run in comparison["runs"]
            ],
        }

    output["finished_at"] = datetime.now(UTC).isoformat()
    output["limitations"] = [
        "Single 12-month sample of 20 Shanghai 50 members; no multi-regime validation.",
        "Unadjusted prices with a frozen corporate-action ledger; no live L2 order book.",
        "Venue fills are modelled from daily bars, not exchange executions.",
    ]
    target = REVIEW / "strategy-portfolio-report.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
