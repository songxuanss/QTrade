"""Prepare terminal research rejection fixtures, never queued vendor work.

Controlled executor/raw/normalization only; SQL adds terminal UI-test jobs after
rejection. No claim of UI creation, leased Worker readiness or live supplier use.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
scope = json.loads((ROOT / ".tools/verification-local/budget-scope.json").read_text())["scope_id"]
actor = json.loads((ROOT / ".tools/verification-local/budget-actors.json").read_text())["admin"]
code = """
import json
from datetime import date
from decimal import Decimal,InvalidOperation
from pathlib import Path
from uuid import UUID,uuid4
import psycopg
from qtrade_collector.config import configuration
from qtrade_data_contracts.research_transport import (
    ResearchCapability,ResearchPlanRequest,ResearchPartition)
from qtrade_data_contracts.planning import CollectionCostEstimate
from qtrade_data_core.research import ResearchService,ResearchRawPage
from qtrade_data_core.research_collection import ResearchCollectionExecutor
from qtrade_data_core.ports import DataFailure
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_http.client import RemoteDataAuthorizer
from qtrade_data_postgres.research_publications import PostgresResearchPublications

params=json.loads(PARAMS_JSON);config=configuration()
auth=RemoteDataAuthorizer(config['COLLECTOR_AUTHORIZER_URL'],config['COLLECTOR_SERVICE_TOKEN'],allow_http=True)
repo=PostgresResearchPublications(config['DATA_COLLECTION_DATABASE_URL'])
archive=FileResearchArchive(Path(config['COLLECTOR_ARCHIVE_ROOT']))
results=[]
for kind,axis in [('financial_statement','report_period'),('corporate_action','event_date')]:
    class FixtureProvider:
        def estimate(self,request,parts):
            return CollectionCostEstimate(calls_min=0,calls_max=0,credits_min=0,credits_max=0,
                accounting='internal_credit_safeguard',includes_retries=False,basis='fixture，无真实调用')
        def capabilities(self):
            return (ResearchCapability(source='controlled.research.normalization.'+kind,
                name='受控研究转换fixture（非供应商）',kind=kind,configured=True,evidence='fixture',
                markets=('CN',),instrument_kinds=('stock',),date_axes=(axis,),
                timezone='Asia/Shanghai',mapping_version='controlled.research.v1',
                limitations=('仅受控拒绝样本，无真实源或发布',)),)
        def partitions(self,request):
            yield ResearchPartition(instrument_id='600519.SH',start=request.start,end=request.end)
        def fetch(self,request,partition,cursor):
            return ResearchRawPage(({'value':'bad-number','fixture':True},),None,None)
        def normalize(self,request,page,collected_at):
            try: Decimal(page.rows[0]['value'])
            # Exercise the typed-adapter rejection boundary, not only an
            # already-translated domain error. The executor must retain raw.
            except InvalidOperation: raise ValueError('invalid fixture number') from None
    provider=FixtureProvider()
    req=ResearchPlanRequest(actor_id=UUID(params['actor']),scope_id=UUID(params['scope']),
        request_id=uuid4(),correlation_id=uuid4(),source=provider.capabilities()[0].source,
        kind=kind,instrument_ids=('600519.SH',),date_axis=axis,
        start=date(2015,1,1),end=date(2016,1,1),mode='all')
    plan=ResearchService(auth,(provider,)).preview(req)
    try: ResearchCollectionExecutor(auth,archive,repo).collect(plan,provider)
    except DataFailure as error: assert error.code=='unverified'
    else: raise AssertionError('fixture unexpectedly normalized')
    failures=archive.processing_failures(plan)
    assert len(failures)==1 and failures[0].normalized_record_count is None
    assert archive.raw(repo.raw_manifest(req.scope_id,failures[0].batch_id))
    with psycopg.connect(config['DATA_COLLECTION_DATABASE_URL']) as conn:
        conn.execute('INSERT INTO qtrade_data.research_job'
            '(scope_id,request_id,state,attempts,error_code) VALUES(%s,%s,%s,%s,%s)',
            (req.scope_id,req.request_id,'failed',1,'unverified'))
    results.append(dict(scope_id=str(req.scope_id),request_id=str(req.request_id),kind=kind,
        batch_id=str(failures[0].batch_id),failure_id=str(failures[0].failure_id)))
print(json.dumps(results))
"""
result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input="PARAMS_JSON=" + repr(json.dumps({"scope": scope, "actor": actor})) + "\n" + code,
    text=True,
    capture_output=True,
)
if result.returncode:
    raise SystemExit("Research rejection fixture preparation failed; private output withheld")
targets = json.loads(result.stdout)
path = ROOT / ".tools/verification-local/research-processing-fixtures.json"
path.write_text(json.dumps(targets, indent=2))
path.chmod(0o600)
print("Prepared 2 controlled terminal research rejections; no vendor/market calls")
