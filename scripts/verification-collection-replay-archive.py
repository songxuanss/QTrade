"""Read-only runtime evidence for exact real archived inputs and offline releases.

The baseline hashes immutable parent files before UI submission. Verification
never contacts a provider and prints only counts/booleans, not supplier bodies.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
review = root / ".tools/verification-local/review"
namespace = os.environ.get("QTRADE_REPLAY_REPORT", "collection-replay-ui")
assert re.fullmatch(r"[a-z0-9-]+", namespace)
mode = sys.argv[1]
assert mode in {"before", "after"}
source = json.loads((review / "normalization-input-ui.json").read_text())
values = {"mode": mode, "original": source}
if mode == "after":
    values["baseline"] = json.loads((review / "collection-replay-parent-proof.json").read_text())
    values["replays"] = json.loads((review / f"{namespace}.json").read_text())
code = """
import json
from hashlib import sha256
from pathlib import Path
from uuid import UUID
from qtrade_collector.config import configuration
from qtrade_data_contracts.collection import CollectionPublication
from qtrade_data_core.collection_replay import CollectionReplayService, lineage
from qtrade_data_core.freshness_evidence import DailyPublicationEvidence
from qtrade_data_core.collection_quality import evaluate
from qtrade_data_core.ports import DataFailure
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_postgres.collection_replay import PostgresCollectionReplays
from qtrade_data_mairui.normalization import normalize
v=json.loads(INPUT_JSON); c=configuration()
repo=PostgresCollectionReplays(c['DATA_COLLECTION_DATABASE_URL'])
archive=FileCollectionArchive(Path(c['COLLECTOR_ARCHIVE_ROOT']))
scope=UUID(v['original']['scope_id']); parents=[]
for run in v['original']['runs']:
 for selected in run['parts']:
  job,pid=UUID(run['job_id']),UUID(selected['part_id'])
  part=repo.part_for_job(scope,job,pid)
  directory=archive.root/str(scope)/str(pid)
  files={str(p.relative_to(archive.root)):sha256(p.read_bytes()).hexdigest()
         for p in directory.rglob('*') if p.is_file()}
  parents.append(dict(part_id=str(pid),files=files))
if v['mode']=='before':
 print(json.dumps(dict(scope_id=str(scope),parents=parents)))
else:
 assert parents==v['baseline']['parents']
 from qtrade_data_postgres.migrate import DIRECTORY
 with repo._connection() as connection:
  query='SELECT version,checksum FROM qtrade_data.schema_migration'
  ledger=dict(connection.execute(query).fetchall())
 files=DIRECTORY.glob('[0-9][0-9][0-9][0-9]_*.sql')
 assert ledger=={p.name:sha256(p.read_bytes()).hexdigest() for p in files}
 results=[]
 for target in v['replays']['targets']:
  parent=repo.part_for_job(scope,UUID(target['parent_job_id']),UUID(target['parent_part_id']))
  intent=repo.replay(scope,UUID(target['replay_id']))
  assert intent and intent.supplier_calls==0
  part=repo.part_for_job(scope,intent.command.request_id,intent.target_part_id)
  original=repo.publication(scope,UUID(target['original_publication_id']))
  pub=repo.publication(scope,UUID(target['replay_publication_id']))
  assert part.state=='completed' and pub.publication_id!=original.publication_id
  inputs=archive.normalization_input(parent,intent.input.input_id)
  assert inputs.input_sha256==intent.input.input_sha256==target['input_sha256']
  result=normalize(part,inputs.payload)
  assert archive.raw_records(pub)==result.raw==archive.raw_records(original)
  assert archive.records(pub)==result.records
  old_records=archive.records(original)
  if part.mapping_version=='mairui.history-bars.v3':
   assert result.records==tuple(r.model_copy(update={'quality':'valid'}) for r in old_records)
   assert result.evidence['source_flag_unknown_count']==0
  else:
   assert result.records==old_records
  assert len(result.records)==target['records']>0
  assert pub.evidence==result.evidence|{'normalization_replay':lineage(intent)}
  assert archive.source_row_proofs(part,pub.published_at)==()
  checks=archive.qualities(part)
  from dataclasses import replace
  result=replace(result,evidence=pub.evidence)
  assert any(q.status in {'passed','warning'} and q.rule_version==intent.rule_version
     and evaluate(part,result,intent.timezone,q.checked_at,source_captured=True)==q for q in checks)
  auth=StaticScopeAuthorizer({f'{intent.command.actor_id}:{scope}':'read'})
  replays=CollectionReplayService(auth,archive,repo,())
  freshness_status='not_applicable'
  if target['kind']=='bars':
   evidence=DailyPublicationEvidence(auth,repo,archive,replays=replays)
   # Legacy suspect classifications cannot certify daily freshness. A new
   # mapping's corrected records need their own complete evidence and gate.
   proof=replays.proofs(intent.command.actor_id,part,pub)
   assert {c.capture_id for c,_ in proof}=={s.capture_id for s in intent.sources}
   if part.mapping_version=='mairui.history-bars.v3':
    new=evidence.verify(intent.command.actor_id,scope,pub.publication_id)
    assert new.collected.observed_at==max(c.captured_at for c,_ in proof if c.kind=='history')
    assert new.collected.observed_at<pub.published_at
    freshness_status='new_mapping_verified_original_acquisition'
   try:
    old=evidence.verify(intent.command.actor_id,scope,original.publication_id)
   except DataFailure as error:
    assert error.code=='unverified' and all(r.quality=='suspect' for r in old_records)
    if part.mapping_version!='mairui.history-bars.v3':
     freshness_status='unverified_original_and_replay'
    try:
     evidence.verify(intent.command.actor_id,scope,pub.publication_id)
    except DataFailure as child_error:
     assert child_error.code=='unverified' and part.mapping_version!='mairui.history-bars.v3'
    else:
     assert part.mapping_version=='mairui.history-bars.v3', 'reprocessing manufactured freshness'
   else:
    new=evidence.verify(intent.command.actor_id,scope,pub.publication_id)
    freshness_status='original_acquisition_preserved'
    assert old.collected.observed_at==new.collected.observed_at < pub.published_at
    assert old.collected.complete_through==new.collected.complete_through
  else:
   assert all(r.available_at is None for r in result.records)
  results.append(dict(kind=target['kind'],field=target['field'],records=len(result.records),
    original_files_unchanged=True,exact_input_conversion=True,quality_verified=True,
    original_acquisition_preserved=True,freshness_status=freshness_status,no_forged_child_receipts=True))
 print(json.dumps(dict(passed=True,migrations=len(ledger),results=results)))
"""
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input="INPUT_JSON=" + repr(json.dumps(values)) + "\n" + code,
    text=True,
    capture_output=True,
)
if result.returncode:
    diagnostic = review / "collection-replay-proof-error.log"
    diagnostic.write_text(result.stderr)
    diagnostic.chmod(0o600)
    raise SystemExit("Replay archive verification failed; private details withheld")
proof = json.loads(result.stdout)
output = review / (
    "collection-replay-parent-proof.json"
    if mode == "before"
    else f"{namespace.removesuffix('-ui')}-proof.json"
)
output.write_text(json.dumps(proof, indent=2))
output.chmod(0o600)
print("PASS parent immutable baseline" if mode == "before" else json.dumps(proof))
