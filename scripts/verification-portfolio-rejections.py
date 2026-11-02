#!/usr/bin/env python3
"""Rejection-path and permission checks for the portfolio acceptance flow.

Runs against the deployed local stack with the real SSO session. Role checks
temporarily downgrade the acceptance membership to ``viewer`` and restore it in
a ``finally`` block; the resulting state is asserted at the end.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / ".tools" / "verification-local" / "review"
DATABASE = "qtrade_rp_test_003507162da945a8953861a46250b5d4"
WORKSPACE = "3c6984b4-5cbf-470c-8d1a-56d1f5b51020"
USER = "6d0b2e8e-9009-4f3a-bdaf-ad855e310b45"
POOL_TWO = "18a1558b-dd37-4961-b850-37473fd8ea9f"
POOL_FIFTY = "bbe141c5-ed97-4224-b76e-efadcd553ec2"

spec = importlib.util.spec_from_file_location(
    "portfolio", ROOT / "scripts" / "verification-strategy-portfolio.py"
)
assert spec and spec.loader
portfolio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portfolio)


def set_role(role: str) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            "qtrade-postgres-1",
            "psql",
            "-U",
            "qtrade",
            "-d",
            DATABASE,
            "-c",
            f"update qtrade.membership set role='{role}' "
            f"where workspace_id='{WORKSPACE}' and user_id='{USER}'",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def call(
    client: httpx.Client, api: Any, method: str, path: str, body: Any | None = None
) -> tuple[int, Any]:
    headers = {"Cookie": f"qtrade-session={api.session}"}
    if method != "GET":
        headers["X-CSRF-Token"] = api.csrf
    response = client.request(
        method, f"{portfolio.API}{path}", headers=headers, json=body, timeout=300
    )
    try:
        payload = response.json()
    except ValueError:
        payload = response.text[:300]
    return response.status_code, payload


def compose_body(size: int, snapshot: str = POOL_FIFTY) -> dict[str, object]:
    return {
        "snapshot_id": snapshot,
        "start": portfolio.DATASET_START,
        "end": portfolio.DATASET_END,
        "selection_size": size,
    }


def main() -> int:
    client = httpx.Client(follow_redirects=False)
    session = portfolio.login(client)
    api = portfolio.Api(client, session)
    results: dict[str, object] = {}
    failures: list[str] = []

    # 1. Not enough pool members with complete coverage.
    status, payload = call(
        client,
        api,
        "POST",
        f"/api/v1/workspaces/{WORKSPACE}/data/datasets/compose",
        compose_body(20, POOL_TWO),
    )
    results["coverage_insufficient"] = {"status": status, "detail": payload}
    if status != 400:
        failures.append(f"coverage rejection expected 400, got {status}")

    # 2. Selection exceeds the 5000-bar replay budget (50 x 242 = 12100).
    status, payload = call(
        client,
        api,
        "POST",
        f"/api/v1/workspaces/{WORKSPACE}/data/datasets/compose",
        compose_body(50),
    )
    results["bar_budget_exceeded"] = {"status": status, "detail": payload}
    if status != 400:
        failures.append(f"bar budget rejection expected 400, got {status}")

    # 3. A version without a completed run cannot claim backtested qualification.
    status, payload = call(
        client,
        api,
        "POST",
        f"/api/v1/workspaces/{WORKSPACE}/strategies",
        {
            "name": "拒绝路径验收 · 未完成回测",
            "description": "Lifecycle guard probe",
            "code": "from qtrade import Strategy\n\n\nclass Noop(Strategy):\n    pass\n",
            "parameter_schema": {"type": "object", "properties": {}},
            "dependencies": {},
        },
    )
    if status != 201:
        raise SystemExit(f"could not create probe strategy: {status} {payload}")
    strategy = payload
    strategy_id = strategy["strategy"]["strategy_id"]
    version = strategy["version"]["version"]
    status, validated = call(
        client,
        api,
        "POST",
        f"/api/v1/workspaces/{WORKSPACE}/strategies/{strategy_id}/versions/{version}"
        "/lifecycle/transitions",
        {"state": "validated", "reason": "probe"},
    )
    results["probe_validated"] = {"status": status, "state": validated.get("state")}
    status, payload = call(
        client,
        api,
        "POST",
        f"/api/v1/workspaces/{WORKSPACE}/strategies/{strategy_id}/versions/{version}"
        "/lifecycle/transitions",
        {"state": "backtested", "reason": "must be refused without a completed run"},
    )
    results["backtested_without_completed_run"] = {"status": status, "detail": payload}
    if not 400 <= status < 500:
        failures.append(f"qualification guard expected rejection, got {status}")
    call(
        client,
        api,
        "POST",
        f"/api/v1/workspaces/{WORKSPACE}/strategies/{strategy_id}/archive",
    )

    # 4. Role restriction: viewer has neither DATA_MANAGE nor backtest write.
    try:
        set_role("viewer")
        status, payload = call(
            client,
            api,
            "POST",
            f"/api/v1/workspaces/{WORKSPACE}/data/datasets/compose",
            compose_body(20),
        )
        results["viewer_compose"] = {"status": status, "detail": payload}
        if status != 403:
            failures.append(f"viewer compose expected 403, got {status}")
        status, payload = call(
            client,
            api,
            "POST",
            f"/api/v1/workspaces/{WORKSPACE}/backtests",
            {
                "start": portfolio.DATASET_START,
                "end": portfolio.DATASET_END,
                "initial_cash": portfolio.INITIAL_CASH,
                "currency": "CNY",
                "cost_model_version": portfolio.COST_MODEL,
                "parameters": {},
                "random_seed": 0,
                "execution_model": portfolio.EXECUTION,
                "dataset_id": "4ad3c536-681b-57d3-b2f1-7fc224093106",
                "universe_mode": "dataset",
            },
        )
        results["viewer_backtest"] = {"status": status, "detail": payload}
        if status != 403:
            failures.append(f"viewer backtest expected 403, got {status}")
    finally:
        set_role("admin")

    status, session_state = call(client, api, "GET", "/auth/session")
    results["restored_role"] = session_state.get("workspace", {}).get("role")

    results["failures"] = failures
    REVIEW.mkdir(parents=True, exist_ok=True)
    (REVIEW / "portfolio-rejections.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(results, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
