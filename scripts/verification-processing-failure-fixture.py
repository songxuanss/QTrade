"""Prepare one clearly labelled rejection fixture in the existing test workspace.

The fixture is processed inside one DB transaction, so the production Worker
never sees its queued state. No vendor calls, fake credits or market publication.
Task creation is fixture preparation, not evidence of UI task creation.
"""

import json
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
state = json.loads((ROOT / ".tools/verification-local/budget-scope.json").read_text())
actors = json.loads((ROOT / ".tools/verification-local/budget-actors.json").read_text())
params = {"scope": state["scope_id"], "actor": actors["admin"], "job": str(uuid4())}
code = """
import json
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from pathlib import Path
from uuid import UUID,uuid4
import psycopg
from qtrade_collector.config import configuration
from qtrade_data_contracts.collection import CollectionJobRequest,SourceDataset
from qtrade_data_contracts.planning import CollectionCostEstimate
from qtrade_data_core.collection import CollectionService
from qtrade_data_core.ports import DataFailure
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_http.client import RemoteDataAuthorizer
from qtrade_data_postgres.jobs import PostgresJobs

params=json.loads(PARAMS_JSON)
config=configuration()
class FixtureProvider:
    def datasets(self):
        return (SourceDataset(source='controlled.normalization.fixture',provider='fixture',
            name='受控转换失败fixture（非真实供应商）',dataset_kind='bars',periods=('5m',),
            instrument_kinds=('stock',),markets=('CN',),timezone='Asia/Shanghai',
            configured=True,evidence='configured',mapping_version='controlled.transform.v1',
            limitations=('只用于受控拒绝验收，非真实来源',)),)
    def names(self,ids): return {key:'受控转换失败fixture' for key in ids}
    def factors(self): return ()
    def partitions(self,request,start,end): yield start,end
    def estimate(self,request,parts):
        return CollectionCostEstimate(calls_min=0,calls_max=0,credits_min=0,credits_max=0,
            accounting='internal_credit_safeguard',includes_retries=False,
            basis='受控fixture，无真实请求或信用消费')
    def fetch(self,part,*,capture):
        rows=[{'t':'bad-time','fixture':True}]
        capture('history',rows)
        try: datetime.fromisoformat(rows[0]['t'])
        except ValueError: raise DataFailure('unverified') from None

req=CollectionJobRequest.model_validate_json(json.dumps(dict(schema_version=1,
    scope_id=params['scope'],actor_id=params['actor'],request_id=params['job'],
    correlation_id=str(uuid4()),source='controlled.normalization.fixture',
    dataset_kind='bars',instrument_ids=['600519.SH'],granularity='5m',
    start='2025-08-31T16:00:00Z',end='2025-09-01T16:00:00Z',mode='all')))
auth=RemoteDataAuthorizer(config['COLLECTOR_AUTHORIZER_URL'],config['COLLECTOR_SERVICE_TOKEN'],allow_http=True)
archive=FileCollectionArchive(Path(config['COLLECTOR_ARCHIVE_ROOT']))
with psycopg.connect(config['DATA_COLLECTION_DATABASE_URL']) as connection:
    class FixtureQueue(PostgresJobs):
        def claim(self,now):
            part=self.part_for_job(req.scope_id,req.request_id,part_id)
            assert part.state=='queued'
            token=uuid4()
            connection.execute('UPDATE qtrade_data.collection_part SET state=%s,attempt=1,'
                'lease_token=%s,lease_until=%s WHERE scope_id=%s AND part_id=%s AND state=%s',
                ('running',token,now+timedelta(minutes=10),req.scope_id,part_id,'queued'))
            return replace(part,state='running',attempt=1,lease_token=token)
    queue=FixtureQueue(config['DATA_COLLECTION_DATABASE_URL'],connection)
    service=CollectionService(auth,queue,archive,(FixtureProvider(),))
    preview=service.preview(req)
    job=service.submit(req,preview.coverage_version)
    part_id=UUID(job['parts'][0]['part_id'])
    service.run_once()
    final=queue.job(req.scope_id,req.request_id)
    assert final['state']=='failed' and final['parts'][0]['publication'] is None
    checks=service.processing_failures(req.actor_id,req.scope_id,req.request_id,part_id)
    assert len(checks)==1 and checks[0].normalized_record_count is None
    print(json.dumps(dict(scope_id=str(req.scope_id),job_id=str(req.request_id),part_id=str(part_id),
        failure_id=str(checks[0].failure_id),
        fixture='controlled raw/normalization rejection; no live supplier claim')))
"""
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input="PARAMS_JSON="
    + repr(json.dumps(params))
    + "\n"
    + "import traceback,sys,json\ntry:\n exec(compile("
    + repr(code)
    + ", 'fixture-worker', 'exec'))\n"
    + "except Exception as error:\n print(json.dumps({'error_type':type(error).__name__,"
    "'frames':[(f.f_code.co_filename.rsplit('/',1)[-1],line) "
    "for f,line in traceback.walk_tb(error.__traceback__)]}))\n sys.exit(1)\n",
    text=True,
    capture_output=True,
)
if result.returncode:
    print(result.stdout.strip())
    raise SystemExit("Controlled fixture preparation failed; private output withheld")
evidence = json.loads(result.stdout)
target = ROOT / ".tools/verification-local/processing-failure-fixture.json"
target.write_text(json.dumps(evidence, indent=2))
target.chmod(0o600)
print("Prepared one controlled normalization rejection; no supplier/market data changed")
