"""Controlled HTTP failures, real isolated PG and private files; not supplier readiness."""

from datetime import timedelta
from uuid import uuid4

import httpx
import psycopg
import pytest
from test_bounded_supplier_http import source as tcp_source_fixture
from test_research_evidence import NOW
from test_research_evidence_postgres import database as isolated_database
from test_research_interface import request

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchService
from qtrade_data_core.research_acquisition import (
    ResearchAcquisitionEvidence,
    ResearchSourceResponse,
)
from qtrade_data_core.research_collection import ResearchCollectionExecutor
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_http.client import StaticScopeAuthorizer
from qtrade_data_mairui.bounded_http import BoundedHttpClient
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_mairui.research import MaiRuiDistributions, MaiRuiFinancial
from qtrade_data_postgres.research import PostgresResearchEvidence
from qtrade_data_postgres.research_acquisition import PostgresResearchResponses

database = isolated_database
tcp_source = tcp_source_fixture


def prepare(database, tmp_path, status, body, *, distributions=False):
    transport = MaiRuiCollection(
        "fixture-only",
        cache=tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(status, content=body))
        ),
    )
    source = (
        MaiRuiDistributions(transport) if distributions else MaiRuiFinancial(transport, "income")
    )
    req = request(
        source=source.source,
        scope_id=uuid4(),
        request_id=uuid4(),
        kind="corporate_action" if distributions else "financial_statement",
        date_axis="event_date" if distributions else "report_period",
    )
    auth = StaticScopeAuthorizer({f"{req.actor_id}:{req.scope_id}": "manage"})
    plan = ResearchService(auth, (source,)).preview(req)
    archive, repository = FileResearchArchive(tmp_path), PostgresResearchEvidence(database)
    store = PostgresResearchResponses(database)
    executor = ResearchCollectionExecutor(
        auth,
        archive,
        repository,
        clock=lambda: NOW,
        acquisition=ResearchAcquisitionEvidence(archive, store),
    )
    return plan, source, archive, repository, store, executor


@pytest.mark.parametrize("distributions", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        b'{"code":403,"code":200,"data":[]}',
        b'[{"value":1,"\\u0076alue":2}]',
        b'[{"value":NaN}]',
        b'[{"value":Infinity}]',
        b'{"code":false,"data":[]}',
        b'{"code":0.0,"data":[]}',
        b'{"code":null,"data":[]}',
        b'[{"value":1e999999999999999999999}]',
    ],
)
def test_ambiguous_json_persists_exact_response_without_raw_or_publication(
    database, tmp_path, distributions, body
):
    plan, source, _, repository, _, executor = prepare(
        database, tmp_path, 200, body, distributions=distributions
    )
    with pytest.raises(DataFailure, match="^unavailable$"):
        executor.collect(plan, source)
    # Reopen both adapters: callback acceptance alone does not prove persistence.
    restarted = PostgresResearchResponses(database)
    (receipt,) = restarted.page(plan, 20, None).receipts
    archive = FileResearchArchive(tmp_path)
    assert receipt.complete and receipt.body_bytes == len(body)
    assert archive.response_body(receipt) == body
    assert repository.stage(plan.request.scope_id, plan.request.request_id) == "registered"
    assert not list(archive.root.glob("*/*/raw-manifest.json"))
    assert not list(archive.root.glob("*/*/artifact-manifest.json"))
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.research_publication WHERE scope_id=%s",
                (plan.request.scope_id,),
            ).fetchone()[0]
            == 0
        )
    assert all(
        path.stat().st_mode & 0o077 == 0 for path in archive.root.rglob("*") if path.is_file()
    )


@pytest.mark.parametrize(
    "status,body,error",
    [
        (401, b"private-fixture-denial", "unauthorized"),
        (200, b"not-json-private-fixture", "unavailable"),
        (200, b'{"code":403,"message":"private-fixture"}', "unauthorized"),
        (200, b'[{"jzrq":"invalid-report"}]', "unverified"),
    ],
)
def test_response_survives_status_decode_and_conversion_failure(
    database, tmp_path, status, body, error
):
    plan, source, archive, repository, store, executor = prepare(database, tmp_path, status, body)
    with pytest.raises(DataFailure, match=error):
        executor.collect(plan, source)
    (receipt,) = store.page(plan, 20, None).receipts
    assert receipt.http_status == status and receipt.body_bytes == len(body)
    assert FileResearchArchive(tmp_path).response_body(receipt) == body
    assert "private-fixture" not in receipt.model_dump_json()
    assert "body_base64" not in receipt.model_dump_json()
    assert not list(archive.root.glob("*/*/artifact-manifest.json"))
    assert repository.stage(plan.request.scope_id, plan.request.request_id) in {
        "registered",
        "raw_persisted",
    }
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.research_publication WHERE scope_id=%s",
                (plan.request.scope_id,),
            ).fetchone()[0]
            == 0
        )


def test_index_failure_aborts_before_decode_but_private_bytes_survive(database, tmp_path):
    plan, source, archive, repository, _, executor = prepare(
        database, tmp_path, 200, b'[{"jzrq":"2015-06-30"}]'
    )

    class FailedIndex:
        def append(self, *args):
            raise DataFailure("unavailable")

    executor.acquisition = ResearchAcquisitionEvidence(archive, FailedIndex())
    with pytest.raises(DataFailure, match="unavailable"):
        executor.collect(plan, source)
    assert repository.stage(plan.request.scope_id, plan.request.request_id) == "registered"
    assert len(list(archive.root.glob("*/*/responses/*.bin"))) == 1
    assert not list(archive.root.glob("*/*/raw-manifest.json"))


def test_immutable_scoped_latest_keyset_pages_and_restart(database, tmp_path):
    plan, _, archive, repository, store, _ = prepare(database, tmp_path, 200, b"[]")
    repository.register(plan)
    values = []
    for index in range(5):
        response = ResearchSourceResponse("research.data", 200, f"[{index}]".encode(), True)
        receipt = archive.capture_response(
            plan,
            plan.partitions[0],
            None,
            uuid4(),
            0,
            response,
            NOW + timedelta(seconds=index // 2),
        )
        store.append(plan, receipt)
        store.append(plan, receipt)
        assert (
            archive.capture_response(
                plan,
                plan.partitions[0],
                None,
                receipt.acquisition_id,
                0,
                response,
                NOW + timedelta(days=1),
            )
            == receipt
        )
        values.append(receipt)
    cursor, received = None, []
    for _ in range(3):
        page = PostgresResearchResponses(database).page(plan, 2, cursor)
        received.extend(page.receipts)
        cursor = page.next_id
    assert cursor is None
    assert received == sorted(
        values, key=lambda r: (r.captured_at, str(r.receipt_id)), reverse=True
    )
    assert len({r.receipt_id for r in received}) == 5
    with pytest.raises(DataFailure, match="invalid_input"):
        store.page(plan, 2, uuid4())
    with pytest.raises(DataFailure, match="conflict"):
        store.append(plan, values[0].model_copy(update={"captured_at": NOW + timedelta(days=1)}))
    with pytest.raises(DataFailure, match="conflict"):
        store.append(plan, values[0].model_copy(update={"scope_id": uuid4()}))
    for sql in (
        "UPDATE qtrade_data.research_response_receipt SET payload=payload",
        "DELETE FROM qtrade_data.research_response_receipt",
        "TRUNCATE qtrade_data.research_response_receipt",
    ):
        with psycopg.connect(database, autocommit=True) as conn, pytest.raises(psycopg.Error):
            conn.execute(sql)
    path = (
        archive.root
        / str(values[0].scope_id)
        / str(values[0].request_id)
        / "responses"
        / f"{values[0].receipt_id}.bin"
    )
    path.write_bytes(b"bad")
    with pytest.raises(DataFailure, match="unverified"):
        archive.response_body(values[0])


@pytest.mark.parametrize(
    "route,error,body",
    [
        ("truncated", "unavailable", b'[{"jzrq":'),
        ("headers", "unavailable", b""),
        ("preheaders", "unavailable", None),
        ("large", "unavailable", b"x" * 65536),
        ("slow", "timeout", "nonempty-prefix"),
    ],
)
def test_real_tcp_partial_response_is_archived_not_published(
    database, tmp_path, tcp_source, route, error, body
):
    plan, source, archive, repository, store, executor = prepare(database, tmp_path, 200, b"[]")
    url, accepted, closed = tcp_source

    class LoopbackOnly(BoundedHttpClient):
        def get(self, _url, *, params=None, timeout=90):
            # Route the production socket-owner path to a controlled TCP peer;
            # no real supplier/key/budget is involved in these fault cases.
            return super().get(url + "/" + route, timeout=2 if route == "slow" else 5)

    source.transport.client = LoopbackOnly(maximum=65536 if route == "large" else 64_000_000)
    with pytest.raises(DataFailure, match=error):
        executor.collect(plan, source)
    assert accepted.is_set() and closed.wait(2)
    receipts = store.page(plan, 20, None).receipts
    if route == "preheaders":
        assert not receipts  # Connection failure must not invent an HTTP response.
    else:
        (receipt,) = receipts
        assert receipt.complete is False and receipt.http_status == 200
        retained = FileResearchArchive(tmp_path).response_body(receipt)
        if route == "slow":
            assert retained and set(retained) == {ord("x")}
        else:
            assert retained == body
        assert receipt.body_bytes == len(retained)
    assert repository.stage(plan.request.scope_id, plan.request.request_id) == "registered"
    assert not list(archive.root.glob("*/*/raw-manifest.json"))
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.research_publication WHERE scope_id=%s",
                (plan.request.scope_id,),
            ).fetchone()[0]
            == 0
        )
