"""Verify scoped freshness snapshots or explicit absence, without writing/probing."""

import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from qtrade_data_contracts.freshness import FreshnessKey, FreshnessResult

ROOT = Path(__file__).resolve().parents[1]
config = dict(
    line.split("=", 1)
    for line in (ROOT / ".env.collector").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
scope = UUID(
    json.loads((ROOT / ".tools/verification-local/budget-scope.json").read_text())["scope_id"]
)
role = os.environ.get("QTRADE_FRESHNESS_ACTOR", "admin")
if role not in {"admin", "reader"}:
    raise SystemExit("invalid verification actor role")
actor = UUID(json.loads((ROOT / ".tools/verification-local/budget-actors.json").read_text())[role])
headers = {
    "Authorization": "Bearer " + config["COLLECTOR_SERVICE_TOKEN"],
    "X-Data-Actor": str(actor),
    "X-Data-Scope": str(scope),
}
with httpx.Client(base_url="http://127.0.0.1:18100", headers=headers, timeout=30) as client:
    assert client.get("/ready").status_code == 200
    catalog = client.get("/v1/catalog")
    assert catalog.status_code == 200
    dataset = next(v for v in catalog.json() if v["source"] == "mairui.history.bars")
    selection = FreshnessKey(
        scope_id=scope,
        source=dataset["source"],
        instrument_id="601288.SH",
        dataset_kind="bars",
        period="1d",
        adjustment="unadjusted",
        date_axis="observation_date",
        mapping_version=dataset["mapping_version"],
    ).model_dump(mode="json")
    # Validate whatever is actually persisted. Absence remains explicit; a
    # historical evidence snapshot is not a fresh-source or UI readiness claim.
    latest = client.post("/v1/freshness/latest", json=selection)
    if latest.status_code == 200:
        snapshot = FreshnessResult.model_validate_json(latest.text)
        assert snapshot.key.model_dump(mode="json") == selection
        latest_check = "latest_scoped_snapshot200"
    else:
        assert latest.status_code == 404 and latest.json() == {"detail": {"code": "not_found"}}
        snapshot = None
        latest_check = "latest_missing404"
    history = client.post("/v1/freshness/history", json={"key": selection})
    assert history.status_code == 200
    records = [FreshnessResult.model_validate_json(json.dumps(v)) for v in history.json()]
    assert all(value.key.model_dump(mode="json") == selection for value in records)
    if snapshot is not None:
        assert snapshot in records
    assert history.headers["cache-control"] == "no-store"
    assert (
        client.post("/v1/freshness/latest", json=selection | {"scope_id": str(uuid4())}).status_code
        == 403
    )
    assert (
        client.post("/v1/freshness/history", json={"key": selection, "limit": True}).status_code
        == 422
    )
    anonymous = httpx.post("http://127.0.0.1:18100/v1/freshness/latest", json=selection)
    assert anonymous.status_code == 401
output = {
    "role": role,
    "checks": [
        "ready",
        "real_authorized_scope",
        latest_check,
        "scoped_history_no_store",
        "foreign_scope403",
        "strict_limit422",
        "anonymous401",
    ],
    "integration": "read only; this script does not create snapshots or request supplier data",
    "ui_and_alerts": "not_verified",
}
path = ROOT / f".tools/verification-local/review/freshness-read-{role}-results.json"
path.write_text(json.dumps(output, indent=2))
path.chmod(0o600)
print(
    "PASS deployed freshness read: scoped snapshot/absence, no-store, "
    "scope/anonymous/bounds rejection"
)
