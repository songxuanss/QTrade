"""Compare fixed post-baseline targets without relaxing failed source outcomes."""

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / ".tools/verification-local/review"


def memory_mib(value: str) -> float:
    matched = re.fullmatch(r"([0-9.]+)([A-Za-z]+)", value.split("/")[0].strip())
    if matched is None:
        raise ValueError("unrecognized resource unit")
    factors = {
        "B": 1 / 1024**2,
        "KiB": 1 / 1024,
        "MiB": 1,
        "GiB": 1024,
        "kB": 1000 / 1024**2,
        "MB": 1000000 / 1024**2,
        "GB": 1000000000 / 1024**2,
    }
    return float(matched[1]) * factors[matched[2]]


def main() -> None:
    baseline = json.loads((ROOT / "collection-baseline-ui.json").read_text())
    retest = json.loads((ROOT / "collection-retest-ui.json").read_text())
    metrics = json.loads((ROOT / "collection-retest-metrics.json").read_text())
    resources = json.loads((ROOT / "collection-retest-resources.json").read_text())
    assert retest.get("completed_at"), "cannot compare a running test"
    for key in ("scope_id", "source", "period", "mode", "start", "end_inclusive", "symbols"):
        assert baseline[key] == retest[key], f"fixed input changed: {key}"
    results = []
    for run in retest["runs"]:
        size = run["size"]
        metric = next(v for v in metrics["runs"] if v["job_id"] == run["job_id"])
        first, last = (datetime.fromisoformat(run[k]) for k in ("started_at", "finished_at"))
        # Compare each run's measured interval, rather than borrowing a later
        # container peak or claiming the discrete samples are a hard ceiling.
        selected = [
            v for v in resources["samples"] if first <= datetime.fromisoformat(v["at"]) <= last
        ]
        peaks = {}
        for sample in selected:
            for container in sample["containers"]:
                name = container["Name"]
                peak = peaks.setdefault(name, {"cpu_pct": 0.0, "memory_mib": 0.0, "samples": 0})
                peak["cpu_pct"] = max(peak["cpu_pct"], float(container["CPUPerc"].rstrip("%")))
                peak["memory_mib"] = max(peak["memory_mib"], memory_mib(container["MemUsage"]))
                peak["samples"] += 1
        checks = {
            "preview": run["preview_ms"] <= 3000,
            "acceptance": run["acceptance_ms"] <= 2000,
            "visible": run.get("visible_ms", float("inf"))
            <= {1: 25000, 5: 60000, 20: 180000}[size],
            "nonempty": run["state"] == "completed"
            and len(run["parts"]) == size
            and all(p["records"] == 243 for p in run["parts"]),
            "consumer_visible": len(run.get("consumer_final", [])) == size
            and {p["part_id"] for p in run["consumer_final"]}
            == {p["part_id"] for p in run["parts"]}
            and all(p["state"] == "visible" for p in run["consumer_final"]),
            "refresh": run["refresh_verified"],
            "collector_memory": peaks.get("qtrade-collector-collector-1", {}).get(
                "memory_mib", float("inf")
            )
            <= 256,
            "postgres_memory": peaks.get("qtrade-collector-collector-db-1", {}).get(
                "memory_mib", float("inf")
            )
            <= 128,
            "ledger": metric["ledger_requests"] == metric["request_events"]
            and metric["ledger_credits"] == metric["request_events"]
            and metric["ledger_response_bytes"] == metric["response_bytes"]
            and metric["ledger_failures"] == 0,
        }
        results.append(
            {
                "size": size,
                "checks": checks,
                "peaks": peaks,
                "preview_ms": run["preview_ms"],
                "acceptance_ms": run["acceptance_ms"],
                "visible_ms": run.get("visible_ms"),
                "metrics": metric,
            }
        )
    assert [v["size"] for v in results] == [1, 5, 20]
    target = ROOT / "collection-performance-targets.json"
    target.write_text(json.dumps({"phase": "retest", "results": results}, indent=2))
    target.chmod(0o600)
    print(json.dumps(results))
    assert all(all(v["checks"].values()) for v in results), (
        "fixed targets not met; preserve failed evidence"
    )


if __name__ == "__main__":
    main()
