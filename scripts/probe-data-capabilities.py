"""Bounded, read-only MaiRui entitlement/semantic probes, with private raw evidence.

This is sample evidence, never proof of market-wide history or PIT readiness.
Credentials and credential-bearing URLs are never written or printed.
"""

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PROBES = (
    ("factor_catalog", "factor/list", {}),
    ("factor_latest", "factor/valuation/600519", {}),
    (
        "factor_history_2025",
        "factor/history/pe_ttm/600519",
        {"st": "20250901", "et": "20250905", "limit": "5"},
    ),
    (
        "factor_history_2015",
        "factor/history/pe_ttm/600519",
        {"st": "20150901", "et": "20150907", "limit": "5"},
    ),
    (
        "pro_one_minute",
        "hsstock/pro/history/600519.SH/1/n",
        {"st": "20250901093000", "et": "20250901113000", "lt": "5"},
    ),
    (
        "financial_factor_history",
        "factor/history/roe_ttm/600519",
        {"st": "20250901", "et": "20250905", "limit": "5"},
    ),
    ("financial_balance", "hsstock/financial/balance/600519", {"st": "20250101", "et": "20251231"}),
    ("financial_income", "hsstock/financial/income/600519", {"st": "20250101", "et": "20251231"}),
    (
        "financial_income_2015",
        "hsstock/financial/income/600519",
        {"st": "20150101", "et": "20151231"},
    ),
    (
        "financial_cashflow",
        "hsstock/financial/cashflow/600519",
        {"st": "20250101", "et": "20251231"},
    ),
    (
        "announcement",
        "hsstock/announcement/600519",
        {"st": "20250901", "et": "20250905", "lt": "5"},
    ),
    ("dividend_events", "hscp/jnfh/600519", {}),
    ("daily_raw", "hsstock/history/600519.SH/d/n", {"st": "20250901", "et": "20250905", "lt": "5"}),
    (
        "daily_forward_adjusted",
        "hsstock/history/600519.SH/d/f",
        {"st": "20250901", "et": "20250905", "lt": "5"},
    ),
    (
        "daily_backward_adjusted",
        "hsstock/history/600519.SH/d/b",
        {"st": "20250901", "et": "20250905", "lt": "5"},
    ),
    (
        "delisted_history_sample",
        "hsstock/history/600005.SH/d/n",
        {"st": "20160901", "et": "20160905", "lt": "5"},
    ),
    ("current_universe_catalog", "hszg/list", {}),
    ("current_index_members", "hszg/gg/hs300", {}),
    (
        "daily_raw_2015",
        "hsstock/history/600519.SH/d/n",
        {"st": "20150901", "et": "20150907", "lt": "5"},
    ),
    (
        "daily_forward_adjusted_2015",
        "hsstock/history/600519.SH/d/f",
        {"st": "20150901", "et": "20150907", "lt": "5"},
    ),
    (
        "north_holding_history",
        "factor/history/north_holding_ratio/600519",
        {"st": "20250901", "et": "20250905", "limit": "5"},
    ),
)


def rows(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        payload = payload.get("data", payload)
        if isinstance(payload, dict):
            payload = payload.get("list", [payload])
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", choices=[item[0] for item in PROBES])
    parser.add_argument("--sample-limit", type=int, default=3, choices=range(1, 101))
    arguments = parser.parse_args()
    selected = arguments.only
    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    licence = env.get("MAIRUI_LICENCE")
    if not licence:
        raise RuntimeError("Configured source authorization is required")
    directory = ROOT / ".tools/data-probe"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    report = {
        "observed_at": datetime.now(UTC).isoformat(),
        "evidence": "live bounded sample",
        "probes": [],
    }
    with httpx.Client(timeout=httpx.Timeout(40, connect=5), follow_redirects=False) as client:
        for name, path, params in PROBES:
            if selected and name not in selected:
                continue
            started = time.monotonic()
            record = {"name": name, "endpoint_template": f"/{path}/<licence>", "params": params}
            try:
                response = client.get(f"https://api.mairuiapi.com/{path}/{licence}", params=params)
                record["http_status"] = response.status_code
                if len(response.content) > 8_000_000:
                    record["outcome"] = "bounded_response_exceeded"
                else:
                    payload = response.json()
                    values = rows(payload)
                    code = payload.get("code") if isinstance(payload, dict) else None
                    accepted = response.status_code == 200 and (
                        code is None or (type(code) is int and code in {0, 200})
                    )
                    if not accepted:
                        values = []
                    sample = json.dumps(
                        values[: arguments.sample_limit], ensure_ascii=False, sort_keys=True
                    ).replace(licence, "<redacted>")
                    sample_hash = hashlib.sha256(sample.encode()).hexdigest()
                    filename = directory / f"{name}-{sample_hash}-sample.json"
                    if filename.exists():
                        if filename.read_text() != sample:
                            raise RuntimeError("Immutable probe sample conflict")
                    else:
                        with filename.open("x") as output:
                            filename.chmod(0o600)
                            output.write(sample)
                    record.update(
                        provider_code=code if type(code) is int else None,
                        record_count=len(values),
                        fields=sorted({key for value in values for key in value}),
                        sample_sha256=sample_hash,
                        sample_file=filename.name,
                        sample_record_count=min(len(values), arguments.sample_limit),
                        outcome="sample_returned" if accepted and values else "not_verified",
                    )
                    if name == "factor_catalog":
                        record["factor_ids"] = sorted(str(v.get("factor_id")) for v in values)
                        record["metadata_fields_present"] = {
                            key: sum(v.get(key) is not None for v in values)
                            for key in ("unit", "update_freq", "calc_version")
                        }
            except (httpx.HTTPError, ValueError):
                record["outcome"] = "transport_or_decode_failure"
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            report["probes"].append(record)
            target = directory / (
                "capabilities-" + "-".join(sorted(selected)) + ".json"
                if selected
                else "capabilities.json"
            )
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            target.chmod(0o600)
            print(
                name,
                record["outcome"],
                record.get("http_status"),
                record.get("record_count"),
                flush=True,
            )
            # Factor-history requests have a higher provider credit cost; keep probes modest.
            time.sleep(max(0, 6 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
