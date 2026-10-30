"""Read-only before/after proof for local recovery, not disaster recovery.

Hash existing collector files/publications and every legacy object. New immutable
outputs may be appended; every previously observed key must remain unchanged.
Private connection and identity values are used only in memory.
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import boto3
from botocore.config import Config
from verification_runtime import _private

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / ".tools/verification-local/review"


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def capture():
    state = json.loads((ROOT / ".tools/sso-local/state.json").read_text())
    startup = json.loads((ROOT / ".tools/verification-local/startup.json").read_text())
    environment = startup["services"]["api"]
    code = """
import hashlib,json
from pathlib import Path
import psycopg
from qtrade_collector.config import configuration
from qtrade_data_contracts.factor_catalog import FactorCatalogSnapshot
c=configuration(); root=Path(c['COLLECTOR_ARCHIVE_ROOT'])
cache = FactorCatalogSnapshot.model_validate_json(
 (root/'provider-cache/factor-catalog-current.v1.json').read_bytes())
assert cache.definitions and cache.content_sha256
files={}
for p in root.rglob('*'):
 if p.is_file():
  h=hashlib.sha256()
  with p.open('rb') as stream:
   for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
  files[str(p.relative_to(root))]=h.hexdigest()
with psycopg.connect(c['DATA_COLLECTION_DATABASE_URL']) as conn:
 bars={str(s)+'/'+str(p):hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()
       for s,p,v in conn.execute('SELECT scope_id,part_id,publication '
                                   'FROM qtrade_data.collection_part '
                                   'WHERE publication IS NOT NULL')}
 research={str(s)+'/'+str(p):hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest()
       for s,p,v in conn.execute('SELECT scope_id,publication_id,manifest '
                                   'FROM qtrade_data.research_publication')}
print(json.dumps(dict(files=files,bars=bars,research=research)))
"""
    result = subprocess.run(
        ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
        input=code,
        capture_output=True,
        text=True,
        check=True,
    )
    archived = json.loads(result.stdout)
    client = boto3.client(
        "s3",
        endpoint_url=environment["OBJECT_STORE_URL"],
        aws_access_key_id=environment["OBJECT_STORE_ACCESS_KEY"],
        aws_secret_access_key=environment["OBJECT_STORE_SECRET_KEY"],
        region_name="us-east-1",
        config=Config(connect_timeout=5, read_timeout=10),
    )
    objects = {}
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=environment["OBJECT_STORE_BUCKET"]
    ):
        for item in page.get("Contents", []):
            with client.get_object(Bucket=environment["OBJECT_STORE_BUCKET"], Key=item["Key"])[
                "Body"
            ] as body:
                h = hashlib.sha256()
                for chunk in iter(lambda: body.read(1024 * 1024), b""):
                    h.update(chunk)
            objects[item["Key"]] = h.hexdigest()
    archived["objects"] = objects
    archived["identity"] = {
        "configuration": checksum(
            {
                key: value
                for key, value in state.items()
                if key not in {"api_url", "sso_url", "prefix"}
            }
        )
    }
    containers = {}
    for name in (
        "qtrade-postgres-1",
        "qtrade-market-db-1",
        "qtrade-object-store-1",
        "qtrade-collector-collector-db-1",
        "qtrade-collector-collector-1",
    ):
        value = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
        containers[name] = checksum({"id": value["Id"], "mounts": value["Mounts"]})
    archived["containers"] = containers
    return archived


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("before", "after"))
    action = parser.parse_args().action
    value = capture()
    path = REVIEW / "runtime-recovery-before.json"
    if action == "before":
        if path.exists():
            raise SystemExit("Baseline already exists; do not overwrite observed evidence")
        _private(path, value)
        print(json.dumps({"baseline": True, "counts": {k: len(v) for k, v in value.items()}}))
        return
    baseline = json.loads(path.read_text())
    _private(REVIEW / "runtime-recovery-after-private.json", value)
    # This one documented current-cache snapshot is atomically replaced by
    # FactorCatalogCache.save after metadata refresh. It is not an immutable
    # batch/manifest. Keep its observed difference in the report; require the
    # live strict contract/digest validation above and preserve every other file.
    mutable = "provider-cache/factor-catalog-current.v1.json"
    mutable_changed = baseline["files"].get(mutable) != value["files"].get(mutable)
    differences = {
        category: sum(
            value[category].get(key) != digest
            for key, digest in items.items()
            if not (category == "files" and key == mutable)
        )
        for category, items in baseline.items()
    }
    if any(differences.values()):
        print(json.dumps({"passed": False, "different_counts": differences}))
        raise SystemExit("Existing data differed; recovery retention is not verified")
    summary = {
        "passed": True,
        "mutable_catalog_snapshot_changed": mutable_changed,
        "current_catalog_contract_valid": True,
        "immutable_files_retained": len(baseline["files"]) - int(mutable in baseline["files"]),
        "retained_counts": {k: len(v) for k, v in baseline.items()},
        "current_counts": {k: len(v) for k, v in value.items()},
    }
    _private(REVIEW / "runtime-recovery-retention.json", summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Runtime evidence failed; private diagnostics withheld") from None
