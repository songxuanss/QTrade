"""Selected market-store persistence acceptance, using actual TimescaleDB."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from qtrade_contracts import Bar
from qtrade_domain.ports import BarQuery, ErrorCode, PortError
from qtrade_postgres.market_data import TimescaleMarketData
from qtrade_postgres.market_migrate import DIRECTORY
from qtrade_postgres.migrate import apply

URL = os.environ.get("QTRADE_TEST_TIMESCALE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requires explicit local TimescaleDB target")


@pytest.fixture
def database():
    schema = "qtrade_market_test_" + uuid4().hex
    apply(URL, schema=schema, directory=DIRECTORY)
    try:
        with psycopg.connect(URL, autocommit=True) as conn:
            conn.execute(sql.SQL("SET search_path TO {},pg_catalog").format(sql.Identifier(schema)))
            yield TimescaleMarketData(URL, schema=schema), conn
    finally:
        with psycopg.connect(URL) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def bars():
    first = Bar.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Bar.json").read_text()
    )
    second = first.model_copy(update={"timestamp": first.timestamp + timedelta(minutes=1)})
    query = BarQuery(
        first.workspace_id,
        first.dataset_id,
        first.instrument_id,
        "1m",
        first.timestamp,
        second.timestamp + timedelta(minutes=1),
        limit=1,
    )
    return first, second, query


def test_concurrent_snapshot_retry_pagination_scope_and_cursor(database):
    store, conn = database
    first, second, query = bars()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: store.write_bars(first.workspace_id, first.dataset_id, (second, first)),
                range(8),
            )
        )
    assert conn.execute("SELECT count(*) FROM market_dataset_snapshot").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM market_bar").fetchone()[0] == 2
    page = store.query_bars(query)
    assert page.bars == (first,) and page.next_cursor is not None
    assert store.query_bars(replace(query, cursor=page.next_cursor)).bars == (second,)
    assert store.query_bars(replace(query, cursor=page.next_cursor)).next_cursor is None
    assert store.query_bars(replace(query, end=second.timestamp)).bars == (first,)
    assert store.query_bars(replace(query, instrument_id="missing")).bars == ()
    with pytest.raises(PortError) as missing:
        store.query_bars(replace(query, workspace_id=uuid4()))
    assert missing.value.code == ErrorCode.NOT_FOUND
    for changed in (
        replace(query, cursor="garbage"),
        replace(query, end=second.timestamp, cursor=page.next_cursor),
    ):
        with pytest.raises(PortError) as invalid:
            store.query_bars(changed)
        assert invalid.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(PortError) as conflict:
        store.write_bars(first.workspace_id, first.dataset_id, (first,))
    assert conflict.value.code == ErrorCode.CONFLICT


def test_empty_snapshot_duplicate_rejection_and_database_immutability(database):
    store, conn = database
    first, _, query = bars()
    store.write_bars(first.workspace_id, first.dataset_id, ())
    assert store.query_bars(query).bars == ()
    new_id = uuid4()
    duplicate = first.model_copy(update={"dataset_id": new_id})
    with pytest.raises(PortError):
        store.write_bars(first.workspace_id, new_id, (duplicate, duplicate))
    assert conn.execute("SELECT count(*) FROM market_dataset_snapshot").fetchone()[0] == 1
    store.write_bars(first.workspace_id, new_id, (duplicate,))
    for table in ("market_dataset_snapshot", "market_bar"):
        for statement in (f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
            with pytest.raises(psycopg.Error):
                conn.execute(statement)
    with pytest.raises(psycopg.Error):
        conn.execute("UPDATE market_bar SET payload='{}'::jsonb")
    with pytest.raises(psycopg.Error):
        conn.execute("UPDATE market_dataset_snapshot SET record_count=0")
    with pytest.raises(psycopg.IntegrityError):
        conn.execute(
            "INSERT INTO market_bar SELECT workspace_id,dataset_id,instrument_id,granularity,"
            "timestamp + interval '1 minute',payload FROM market_bar"
        )
    assert conn.execute("SELECT count(*) FROM market_bar").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT count(*) FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema=current_schema()",
        ).fetchone()[0]
        == 1
    )


def test_failed_copy_rolls_back_snapshot_and_all_bars(database):
    store, conn = database
    first, second, _ = bars()
    conn.execute("""
        CREATE FUNCTION reject_second_test_bar() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.timestamp > '2026-10-01T00:00:00Z'::timestamptz THEN
                RAISE EXCEPTION 'injected copy failure';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER reject_second_test_bar BEFORE INSERT ON market_bar
            FOR EACH ROW EXECUTE FUNCTION reject_second_test_bar();
    """)
    with pytest.raises(PortError) as failure:
        store.write_bars(first.workspace_id, first.dataset_id, (first, second))
    assert failure.value.code == ErrorCode.UNAVAILABLE
    assert conn.execute("SELECT count(*) FROM market_dataset_snapshot").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM market_bar").fetchone()[0] == 0


def test_concurrent_new_datasets_across_chunks_and_semantic_retry(database):
    from datetime import timezone
    from decimal import Decimal

    store, conn = database
    first, _, _ = bars()
    items = tuple(
        first.model_copy(
            update={
                "dataset_id": uuid4(),
                "timestamp": first.timestamp + timedelta(days=index * 14),
            }
        )
        for index in range(8)
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda item: store.write_bars(item.workspace_id, item.dataset_id, (item,)), items
            )
        )
    equivalent = items[0].model_copy(
        update={
            "open": Decimal("10.000"),
            "timestamp": items[0].timestamp.astimezone(timezone(timedelta(hours=8))),
        }
    )
    store.write_bars(equivalent.workspace_id, equivalent.dataset_id, (equivalent,))
    assert conn.execute("SELECT count(*) FROM market_dataset_snapshot").fetchone()[0] == 8
    assert conn.execute("SELECT count(*) FROM market_bar").fetchone()[0] == 8
    assert (
        conn.execute(
            "SELECT count(*) FROM timescaledb_information.chunks "
            "WHERE hypertable_schema=current_schema()"
        ).fetchone()[0]
        >= 8
    )


@pytest.fixture
def identity_schema():
    schema = "qtrade_identity_test_" + uuid4().hex
    apply(URL, schema=schema)
    try:
        yield schema
    finally:
        with psycopg.connect(URL) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_raw_replay_persists_new_versions_and_recovers_publication_without_changing_history(
    database, identity_schema
):
    from datetime import UTC, datetime

    from qtrade_application.batches import RawBatchService
    from qtrade_application.datasets import DatasetReplayService
    from qtrade_contracts import BacktestRun
    from qtrade_domain.bar_json import StandardBarJsonTransformation
    from qtrade_domain.batches import BatchInput
    from qtrade_domain.datasets import ReplayRequest
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_domain.raw_records import RawRecord
    from qtrade_mock.ports import MockObjectStore
    from qtrade_parquet.raw_records import ParquetRawRecordCodec
    from qtrade_postgres.batches import PostgresBatchRepository
    from qtrade_postgres.datasets import PostgresDatasetRepository
    from qtrade_postgres.identity import PostgresIdentityStore
    from qtrade_postgres.workspaces import PostgresWorkspaceStore

    market, conn = database
    owner = PostgresIdentityStore(URL, schema=identity_schema).initialize(
        VerifiedIdentity("https://sso.test", "replay-owner", "Replay owner"),
        workspace_name="Replay",
    )
    access = PostgresWorkspaceStore(URL, schema=identity_schema).access(
        owner.user.user_id, owner.personal_workspace.workspace_id
    )
    assert access is not None
    bar = bars()[0].model_copy(
        update={"workspace_id": access.workspace.workspace_id, "batch_id": uuid4()}
    )
    objects = MockObjectStore()
    batches = RawBatchService(
        PostgresBatchRepository(URL, schema=identity_schema), objects, ParquetRawRecordCodec()
    )
    draft = BatchInput(
        bar.workspace_id,
        bar.batch_id,
        "qtrade.standard-bar-json",
        "test-license",
        uuid4(),
        bar.instrument_id,
        "1m",
        datetime.now(UTC),
        (RawRecord(0, bar.model_dump_json().encode(), "application/json"),),
    )
    raw = batches.publish(access, draft)
    repository = PostgresDatasetRepository(URL, schema=identity_schema)

    class Revised(StandardBarJsonTransformation):
        version = "test.revised-quality.v2"

        def transform(self, dataset_id, source_batches):
            return tuple(
                item.model_copy(update={"quality": "suspect"})
                for item in super().transform(dataset_id, source_batches)
            )

    service = DatasetReplayService(
        repository, batches, market, objects, [StandardBarJsonTransformation(), Revised()]
    )
    request = ReplayRequest(
        bar.workspace_id,
        uuid4(),
        (bar.batch_id,),
        StandardBarJsonTransformation.version,
        bar.timestamp,
        bar.timestamp + timedelta(minutes=1),
        datetime.now(UTC),
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        first_results = tuple(pool.map(lambda _: service.replay(access, request), range(8)))
    first = first_results[0]
    assert all(item == first for item in first_results)
    old_query = BarQuery(
        bar.workspace_id, request.dataset_id, bar.instrument_id, "1m", request.start, request.end
    )
    old_bars = market.query_bars(old_query).bars
    old_run = BacktestRun.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/BacktestRun.json").read_text()
    ).model_copy(update={"workspace_id": bar.workspace_id, "dataset_id": request.dataset_id})
    revised = replace(
        request,
        dataset_id=uuid4(),
        parent_dataset_id=request.dataset_id,
        transformation_version=Revised.version,
    )
    second = service.replay(access, revised)
    assert second.metadata.version == 2 and second.parent_dataset_id == first.metadata.dataset_id
    assert (
        market.query_bars(replace(old_query, dataset_id=revised.dataset_id)).bars[0].quality
        == "suspect"
    )
    assert market.query_bars(old_query).bars == old_bars

    assert repository.get(bar.workspace_id, request.dataset_id) == first
    assert batches.read(access, bar.batch_id).metadata == raw
    assert batches.read(access, bar.batch_id).records == draft.records
    assert old_run.dataset_id == request.dataset_id
    assert conn.execute("SELECT count(*) FROM market_dataset_snapshot").fetchone()[0] == 2

    class FailOnce(PostgresDatasetRepository):
        failed = False

        def put(self, actor, publication):
            if not self.failed:
                self.failed = True
                raise PortError(ErrorCode.UNAVAILABLE, "test publication failure")
            super().put(actor, publication)

    recovering = DatasetReplayService(
        FailOnce(URL, schema=identity_schema), batches, market, objects, [Revised()]
    )
    third_request = replace(revised, dataset_id=uuid4(), parent_dataset_id=revised.dataset_id)
    with pytest.raises(PortError) as failed:
        recovering.replay(access, third_request)
    assert failed.value.code == ErrorCode.UNAVAILABLE
    with pytest.raises(PortError) as unpublished:
        repository.get(bar.workspace_id, third_request.dataset_id)
    assert unpublished.value.code == ErrorCode.NOT_FOUND
    assert conn.execute("SELECT count(*) FROM market_dataset_snapshot").fetchone()[0] == 3
    third = recovering.replay(access, third_request)
    assert third.metadata.version == 3
    assert repository.get(bar.workspace_id, third_request.dataset_id) == third
    assert conn.execute("SELECT count(*) FROM market_dataset_snapshot").fetchone()[0] == 3
    assert market.query_bars(old_query).bars == old_bars

    with psycopg.connect(URL) as core:
        core.execute(
            sql.SQL("UPDATE {}.membership SET role='viewer' WHERE user_id=%s").format(
                sql.Identifier(identity_schema)
            ),
            (owner.user.user_id,),
        )
    denied_request = replace(
        third_request, dataset_id=uuid4(), parent_dataset_id=third_request.dataset_id
    )
    with pytest.raises(PortError) as denied:
        recovering.replay(access, denied_request)
    assert denied.value.code == ErrorCode.REJECTED
    with pytest.raises(PortError) as still_unpublished:
        repository.get(bar.workspace_id, denied_request.dataset_id)
    assert still_unpublished.value.code == ErrorCode.NOT_FOUND
    assert market.query_bars(old_query).bars == old_bars


def test_historical_ingestion_worker_completes_after_restart_with_postgres_parquet_and_timescale(
    database, identity_schema
):
    from datetime import UTC, datetime
    from pathlib import Path

    from qtrade_application.batches import RawBatchService
    from qtrade_application.datasets import DatasetReplayService
    from qtrade_application.ingestion import HistoricalIngestionWorker
    from qtrade_application.lineage import DataLineageService
    from qtrade_application.market_data import MarketDataService
    from qtrade_contracts import Bar
    from qtrade_domain.bar_json import StandardBarJsonTransformation
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_domain.ingestion import CollectionPolicy, CollectionRequest, ProviderPage
    from qtrade_domain.quality import QualityIssue
    from qtrade_domain.raw_records import RawRecord
    from qtrade_mock.ports import MockObjectStore
    from qtrade_parquet.raw_records import ParquetRawRecordCodec
    from qtrade_postgres.batches import PostgresBatchRepository
    from qtrade_postgres.datasets import PostgresDatasetRepository
    from qtrade_postgres.identity import PostgresIdentityStore
    from qtrade_postgres.ingestion import PostgresIngestionRepository
    from qtrade_postgres.quality import PostgresQualityIssueStore
    from qtrade_postgres.workspaces import PostgresWorkspaceStore

    market, conn = database
    owner = PostgresIdentityStore(URL, schema=identity_schema).initialize(
        VerifiedIdentity("https://sso.test", "collector", "Collector"), workspace_name="Collect"
    )
    access = PostgresWorkspaceStore(URL, schema=identity_schema).access(
        owner.user.user_id, owner.personal_workspace.workspace_id
    )
    assert access is not None
    now = datetime(2026, 10, 1, tzinfo=UTC)
    first = Bar.model_validate_json(
        (Path(__file__).parent / "fixtures/contracts/Bar.json").read_text()
    )
    raw_bars = (
        first.model_copy(
            update={
                "workspace_id": access.workspace.workspace_id,
                "timestamp": now,
                "batch_id": uuid4(),
            }
        ),
        first.model_copy(
            update={
                "workspace_id": access.workspace.workspace_id,
                "timestamp": now + timedelta(minutes=1),
                "batch_id": uuid4(),
            }
        ),
    )
    records = tuple(
        RawRecord(index, item.model_dump_json().encode(), "application/json")
        for index, item in enumerate(raw_bars)
    )

    class Provider:
        source = "qtrade.standard-bar-json"

        def __init__(self):
            self.cursors = []

        def fetch(self, request, cursor):
            self.cursors.append(cursor)
            if cursor is None:
                return ProviderPage((records[0],), "next-page")
            return ProviderPage((RawRecord(0, records[1].payload, records[1].content_type),), None)

    provider, objects = Provider(), MockObjectStore()
    raw = RawBatchService(
        PostgresBatchRepository(URL, schema=identity_schema), objects, ParquetRawRecordCodec()
    )
    repository = PostgresDatasetRepository(URL, schema=identity_schema)
    transformation = StandardBarJsonTransformation()
    datasets = DatasetReplayService(repository, raw, market, objects, [transformation])
    tasks = PostgresIngestionRepository(URL, schema=identity_schema)
    worker = HistoricalIngestionWorker(
        tasks, raw, datasets, (provider,), CollectionPolicy(min_interval_seconds=1)
    )
    request = CollectionRequest(
        access.workspace.workspace_id,
        uuid4(),
        provider.source,
        "fixture-license",
        first.instrument_id,
        "1m",
        now,
        now + timedelta(minutes=2),
        now,
        transformation.version,
        10,
    )
    task = worker.submit(access, request)
    checkpoint = worker.run_once(access, request.task_id, uuid4(), now)
    assert checkpoint.cursor == "next-page" and len(checkpoint.batch_ids) == 1
    # Rebuild all process services: the next provider cursor and batch are read from PostgreSQL.
    worker = HistoricalIngestionWorker(
        PostgresIngestionRepository(URL, schema=identity_schema),
        raw,
        datasets,
        (provider,),
        CollectionPolicy(min_interval_seconds=1),
    )
    completed_page = worker.run_once(access, request.task_id, uuid4(), now + timedelta(seconds=1))
    assert completed_page.collection_complete and len(completed_page.batch_ids) == 2
    completed = worker.run_once(access, request.task_id, uuid4(), now + timedelta(seconds=2))
    assert completed.status == "completed" and completed.dataset_id == request.dataset_id
    stored = repository.get(request.workspace_id, request.dataset_id)
    assert stored.metadata.batch_ids == completed.batch_ids
    query = BarQuery(
        request.workspace_id,
        request.dataset_id,
        first.instrument_id,
        "1m",
        now,
        now + timedelta(minutes=2),
    )
    bars = market.query_bars(query).bars
    assert [item.timestamp for item in bars] == [now, now + timedelta(minutes=1)]
    assert tuple(provider.cursors) == (None, "next-page")
    assert conn.execute("SELECT count(*) FROM market_dataset_snapshot").fetchone()[0] == 1
    with psycopg.connect(URL) as core:
        core.execute(
            sql.SQL("SET search_path TO {},pg_catalog").format(sql.Identifier(identity_schema))
        )
        assert (
            core.execute(
                "SELECT count(*) FROM market_dataset_batch WHERE dataset_id=%s",
                (request.dataset_id,),
            ).fetchone()[0]
            == 2
        )
    assert raw.read(access, task.request.batch_id(0)).records == (records[0],)
    issue = QualityIssue(
        uuid4(),
        request.workspace_id,
        request.dataset_id,
        "price_jump",
        "warning",
        request.instrument_id,
        request.granularity,
        request.start,
        request.start + timedelta(minutes=1),
        (request.batch_id(0),),
        '{"threshold":"0.25"}',
        now + timedelta(minutes=1),
    )
    issue_store = PostgresQualityIssueStore(URL, schema=identity_schema)
    issue_store.record_scan(access.user_id, (issue,))
    lineage = DataLineageService(
        datasets, raw, MarketDataService(market), tasks, issue_store
    ).for_bar(
        access,
        request.dataset_id,
        request.instrument_id,
        request.granularity,
        request.start,
    )
    assert lineage.batch.batch_id == request.batch_id(0)
    assert lineage.collection.status == "completed"
    assert lineage.dataset.metadata.transformation_version == transformation.version
    assert lineage.quality_issues == (issue,)


def test_authenticated_http_uses_real_sessions_memberships_and_timescale(database, identity_schema):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from qtrade_api.market_data import market_data_router
    from qtrade_api.security import ApiSecurity
    from qtrade_application.market_data import MarketDataService
    from qtrade_application.sessions import SessionService, TokenSet
    from qtrade_application.workspaces import WorkspaceService
    from qtrade_crypto.tokens import TokenCipher
    from qtrade_domain.identity_port import VerifiedIdentity
    from qtrade_postgres.audit import PostgresAuditStore
    from qtrade_postgres.identity import PostgresIdentityStore
    from qtrade_postgres.sessions import PostgresSessionStore
    from qtrade_postgres.workspaces import PostgresWorkspaceStore
    from qtrade_runtime.cookies import SessionCookie

    store, conn = database
    identity = PostgresIdentityStore(URL, schema=identity_schema)
    owner, other = [
        identity.initialize(VerifiedIdentity("https://sso.test", name, name), workspace_name=name)
        for name in ("bar-owner", "bar-other")
    ]
    sessions = SessionService(
        PostgresSessionStore(URL, schema=identity_schema),
        TokenCipher({"test": b"a" * 32}, primary="test"),
    )
    session = sessions.create(owner, TokenSet("private-access-token"))
    app = FastAPI()
    app.include_router(
        market_data_router(
            MarketDataService(store),
            ApiSecurity(
                sessions,
                WorkspaceService(PostgresWorkspaceStore(URL, schema=identity_schema)),
                SessionCookie("test", insecure_local=True),
                PostgresAuditStore(URL, schema=identity_schema),
            ),
        )
    )
    client = TestClient(app)
    workspace = owner.personal_workspace.workspace_id
    first, second, _ = bars()
    first = first.model_copy(update={"workspace_id": workspace})
    second = second.model_copy(update={"workspace_id": workspace})
    root = f"/api/v1/workspaces/{workspace}/datasets/{first.dataset_id}/bars"
    params = {
        "instrument_id": first.instrument_id,
        "granularity": "1m",
        "start": first.timestamp.isoformat(),
        "end": (second.timestamp + timedelta(minutes=1)).isoformat(),
        "limit": 1,
    }
    assert client.get(root, params=params).status_code == 401
    client.cookies.set("qtrade-session", session.cookie)
    payload = {"bars": [item.model_dump(mode="json") for item in (second, first)]}
    assert client.post(root, json=payload).status_code == 403
    headers = {"X-CSRF-Token": session.csrf}
    assert client.post(root, json=payload, headers=headers).status_code == 200
    assert client.post(root, json=payload, headers=headers).status_code == 200
    page = client.get(root, params=params).json()
    assert page["bars"] == [first.model_dump(mode="json")]
    assert client.get(root, params={**params, "cursor": page["next_cursor"]}).json()["bars"] == [
        second.model_dump(mode="json")
    ]
    assert (
        client.get(
            root,
            params={**params, "end": second.timestamp.isoformat(), "cursor": page["next_cursor"]},
        ).status_code
        == 400
    )
    assert (
        client.get(
            root.replace(str(workspace), str(other.personal_workspace.workspace_id)), params=params
        ).status_code
        == 404
    )
    with psycopg.connect(URL) as auth_conn:
        auth_conn.execute(
            sql.SQL(
                "UPDATE {}.membership SET role='viewer' WHERE user_id=%s AND workspace_id=%s"
            ).format(sql.Identifier(identity_schema)),
            (owner.user.user_id, workspace),
        )
    assert client.post(root, json=payload, headers=headers).status_code == 403
    assert client.get(root, params=params).status_code == 200
    assert conn.execute("SELECT count(*) FROM market_bar").fetchone()[0] == 2


def test_indexed_adapter_queries_have_bounded_latency_and_correct_selectivity(database):
    import json
    from statistics import median
    from time import perf_counter

    store, conn = database
    first, _, query = bars()
    items = tuple(
        first.model_copy(
            update={
                "instrument_id": f"MOCK:{index % 100:03d}",
                "timestamp": first.timestamp + timedelta(minutes=index // 100),
            }
        )
        for index in range(100_000)
    )
    started = perf_counter()
    store.write_bars(first.workspace_id, first.dataset_id, items)
    load_seconds = perf_counter() - started
    conn.execute("ANALYZE market_bar")
    selected = replace(
        query, instrument_id="MOCK:042", end=first.timestamp + timedelta(minutes=1000), limit=1000
    )
    samples = []
    for _ in range(10):
        started = perf_counter()
        page = store.query_bars(selected)
        samples.append(perf_counter() - started)
        assert len(page.bars) == 1000 and page.next_cursor is None
        assert all(bar.instrument_id == "MOCK:042" for bar in page.bars)
        assert page.bars[0].timestamp == first.timestamp
        assert page.bars[-1].timestamp == first.timestamp + timedelta(minutes=999)
    p95_seconds = sorted(samples)[-1]
    assert p95_seconds < 1, "local indexed 1000-bar query exceeded the 1s regression budget"
    report = {
        "rows": 100_000,
        "instruments": 100,
        "returned_bars": 1000,
        "samples": samples,
        "load_seconds": load_seconds,
        "median_seconds": median(samples),
        "p95_seconds": p95_seconds,
        "regression_budget_seconds": 1,
        "scope": (
            "local adapter including connection and contract validation; not production capacity"
        ),
    }
    target = os.environ.get("QTRADE_TIMESCALE_REPORT")
    if target:
        Path(target).write_text(json.dumps(report, indent=2) + "\n")
