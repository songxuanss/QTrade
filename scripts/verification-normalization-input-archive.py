"""Read-only exact-UI checkpoint/RAW/quality/standard binding, no supplier calls."""

import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
review = root / ".tools/verification-local/review"
value = json.loads((review / "normalization-input-ui.json").read_text())
assert len(value["runs"]) == 2 and all(r.get("state") == "completed" for r in value["runs"])
code = """
import json
from pathlib import Path
from uuid import UUID
from qtrade_collector.config import configuration
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_postgres.jobs import PostgresJobs
from qtrade_data_mairui.normalization import normalize
from qtrade_data_core.collection_quality import evaluate
from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.normalization_input import BarSourceSlice

try:
    BarSourceSlice.model_validate_json(json.dumps(dict(
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
        rows=[{"value": float("nan")}],
    )))
except ValueError:
    pass
else:
    raise AssertionError("runtime nonfinite guard missing")

config = configuration()
v = json.loads(INPUT_JSON)
repo = PostgresJobs(config["DATA_COLLECTION_DATABASE_URL"])
archive = FileCollectionArchive(Path(config["COLLECTOR_ARCHIVE_ROOT"]))
scope = UUID(v["scope_id"])
results = []
for run in v["runs"]:
    for p in run["parts"]:
        part = repo.part_for_job(scope, UUID(run["job_id"]), UUID(p["part_id"]))
        pub = repo.publication(scope, UUID(p["publication_id"]))
        inputs = archive.normalization_inputs(part)
        assert len(inputs) == 1
        value = inputs[0]
        proofs = archive.source_row_proofs(part, pub.published_at)
        refs = {receipt.capture_id: receipt for receipt, _ in proofs}
        assert set(value.raw_capture_ids) <= set(refs)
        assert all(
            refs[i].captured_at <= value.prepared_at <= pub.published_at
            for i in value.raw_capture_ids
        )
        assert any(
            refs[i].kind in {"history", "cached_history", "factor_history"}
            for i in value.raw_capture_ids
        )
        result = normalize(part, value.payload)
        assert archive.raw_records(pub) == result.raw
        assert archive.records(pub) == result.records
        assert len(result.raw) == len(result.records) == p["records"] > 0
        assert result.units == pub.units and result.evidence == pub.evidence
        rows = set().union(
            *(
                hashes
                for receipt, hashes in proofs
                if receipt.kind in {"history", "cached_history", "factor_history"}
            )
        )
        assert {digest(row) for row in result.raw} <= rows
        quality = archive.qualities(part)
        assert any(
            evaluate(part, result, "Asia/Shanghai", q.checked_at, source_captured=True) == q
            for q in quality
        )
        if run["kind"] == "factors":
            assert value.payload.definition.factor_id == p["field"]
            assert all(
                r.calc_version == value.payload.definition.calc_version
                and r.available_at is None
                for r in result.records
            )
        results.append(
            dict(
                job_id=run["job_id"],
                part_id=p["part_id"],
                kind=run["kind"],
                input_id=str(value.input_id),
                input_sha256=value.input_sha256,
                records=len(result.records),
                checkpoint_and_source_valid=True,
                standard_identical=True,
                quality_reproduced=True,
                checkpoint_rows=(
                    sum(len(s.rows) for s in value.payload.slices)
                    if run["kind"] == "bars"
                    else len(value.payload.rows)
                ),
            )
        )
print(json.dumps(dict(parts=results, offline_normalization=True, runtime_finite_guard=True)))
"""
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input="INPUT_JSON=" + repr(json.dumps(value)) + "\n" + code,
    text=True,
    capture_output=True,
)
if result.returncode:
    raise SystemExit("Normalization input binding failed; private details withheld")
proof = json.loads(result.stdout)
p = review / "normalization-input-archive-proof.json"
p.write_text(json.dumps(proof, indent=2))
p.chmod(0o600)
print(
    "PASS exact UI input/source hashes, pure RAW/standard/quality reproduction "
    "and unknown factor knowledge"
)
