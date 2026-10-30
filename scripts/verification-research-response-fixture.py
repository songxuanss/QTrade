"""Prepare terminal HTTP response retention fixtures, never queued vendor work.

Controlled executor/raw/normalization only; SQL adds terminal UI-test jobs after
rejection. No claim of UI creation, leased Worker readiness or live supplier use.
"""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--partial", action="store_true", help="Controlled incomplete transport replies"
)
partial = parser.parse_args().partial
scope = json.loads((ROOT / ".tools/verification-local/budget-scope.json").read_text())["scope_id"]
actor = json.loads((ROOT / ".tools/verification-local/budget-actors.json").read_text())["admin"]
code = """
import json
from datetime import date
from pathlib import Path
from uuid import UUID,uuid4
import httpx,psycopg
from qtrade_collector.config import configuration
from qtrade_data_contracts.research_transport import ResearchPlanRequest
from qtrade_data_core.research import ResearchService
from qtrade_data_core.research_collection import ResearchCollectionExecutor
from qtrade_data_core.research_acquisition import ResearchAcquisitionEvidence
from qtrade_data_core.ports import DataFailure
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_http.client import RemoteDataAuthorizer
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_mairui.bounded_http import BoundedHttpClient,TransportFailure
from qtrade_data_mairui.research import MaiRuiFinancial,MaiRuiDistributions
from qtrade_data_postgres.research_publications import PostgresResearchPublications
from qtrade_data_postgres.research_acquisition import PostgresResearchResponses
params=json.loads(PARAMS_JSON);config=configuration()
auth=RemoteDataAuthorizer(config['COLLECTOR_AUTHORIZER_URL'],config['COLLECTOR_SERVICE_TOKEN'],allow_http=True)
repo=PostgresResearchPublications(config['DATA_COLLECTION_DATABASE_URL'])
store=PostgresResearchResponses(config['DATA_COLLECTION_DATABASE_URL'])
archive=FileResearchArchive(Path(config['COLLECTOR_ARCHIVE_ROOT']))
results=[]
for kind,axis,status,body,error in [
    ('financial_statement','report_period',401,b'controlled fixture denial','unauthorized'),
    ('corporate_action','event_date',200,b'controlled invalid JSON','unavailable')]:
    transport=MaiRuiCollection('fixture-only',cache=Path('/tmp/qtrade-response-fixture-cache'),
        client=httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(status,content=body))))
    if params['partial']:
        status=200
        body=b'controlled incomplete prefix' if kind=='financial_statement' else b''
        error='timeout' if kind=='financial_statement' else 'unavailable'
        class PartialTransport(BoundedHttpClient):
            def get(self,url,*,params=None,timeout=90):
                raise TransportFailure(error,response_status=status,response_body=body)
        transport.client=PartialTransport()
    base=(MaiRuiFinancial(transport,'income') if kind=='financial_statement'
          else MaiRuiDistributions(transport))
    class Fixture(type(base)):
        def capabilities(self):
            return tuple(c.model_copy(update={
                'name':'受控HTTP响应fixture（非供应商）','evidence':'fixture'})
                         for c in super().capabilities())
    provider=Fixture(transport,'income') if kind=='financial_statement' else Fixture(transport)
    provider.source='verification.research.response.'+('partial.' if params['partial'] else '')+kind
    req=ResearchPlanRequest(actor_id=UUID(params['actor']),scope_id=UUID(params['scope']),
        request_id=uuid4(),correlation_id=uuid4(),source=provider.source,kind=kind,
        instrument_ids=('600519.SH',),date_axis=axis,start=date(2015,1,1),end=date(2016,1,1),mode='all')
    plan=ResearchService(auth,(provider,)).preview(req)
    repetitions=21 if kind=='financial_statement' and not params['partial'] else 1
    for _ in range(repetitions):
        try:
            ResearchCollectionExecutor(auth,archive,repo,acquisition=ResearchAcquisitionEvidence(archive,store)).collect(plan,provider)
        except DataFailure as failure: assert failure.code==error
        else: raise AssertionError('controlled response unexpectedly parsed')
    receipts=store.page(plan,100,None).receipts
    assert len(receipts)==repetitions
    receipt=receipts[0]
    assert all(r.http_status==status and archive.response_body(r)==body for r in receipts)
    assert all(r.complete is (not params['partial']) for r in receipts)
    assert repo.stage(req.scope_id,req.request_id)=='registered'
    with psycopg.connect(config['DATA_COLLECTION_DATABASE_URL']) as conn:
        conn.execute('INSERT INTO qtrade_data.research_job'
            '(scope_id,request_id,state,attempts,error_code) VALUES(%s,%s,%s,%s,%s)',
            (req.scope_id,req.request_id,'failed',repetitions,error))
    results.append(dict(scope_id=str(req.scope_id),request_id=str(req.request_id),kind=kind,
                        receipt=receipt.model_dump(mode='json'),receipt_count=repetitions,fixture=True,publication=None))
print(json.dumps(results))
"""

result = subprocess.run(
    ["docker", "exec", "-i", "qtrade-collector-collector-1", "python", "-"],
    input="PARAMS_JSON="
    + repr(json.dumps({"scope": scope, "actor": actor, "partial": partial}))
    + "\n"
    + code,
    text=True,
    capture_output=True,
)
if result.returncode:
    raise SystemExit("Research response fixture preparation failed; private output withheld")
targets = json.loads(result.stdout)
name = "research-response-partial-fixtures.json" if partial else "research-response-fixtures.json"
path = ROOT / ".tools/verification-local" / name
path.write_text(json.dumps(targets, indent=2))
path.chmod(0o600)
print("Prepared 2 controlled terminal HTTP response fixtures; no vendor/market calls")
