"""Isolated real PG dispatch evidence; controlled plans are not supplier calls."""

from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from test_independent_collection import START, part, request
from test_research_evidence_postgres import PrivateDatabaseUrl
from test_research_evidence_postgres import database as isolated_database

from qtrade_data_postgres.jobs import PostgresJobs
from qtrade_data_postgres.migrate import apply

root_database = isolated_database


@pytest.fixture
def database(root_database):
    # The shared suite intentionally leaves a queued research run. Each dispatch
    # case needs its own eligible population to prove a two-turn fairness bound.
    name = "research_test_fair_" + uuid4().hex
    config = conninfo_to_dict(root_database)
    assert config["dbname"].startswith("research_test_")
    with psycopg.connect(root_database, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        url = PrivateDatabaseUrl(make_conninfo(**(config | {"dbname": name})))
        apply(url)
        yield url
    finally:
        with psycopg.connect(root_database, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


@pytest.fixture
def queue(database):
    scopes = set()

    def enqueue(count, *, source=None, scope=None):
        scope = scope or uuid4()
        scopes.add(scope)
        req = request(
            scope_id=scope,
            source=source or "fixture." + uuid4().hex,
            end=START + timedelta(days=count + 1),
        )
        parts = tuple(
            replace(part(req), start=START + timedelta(days=i), end=START + timedelta(days=i + 1))
            for i in range(count)
        )
        PostgresJobs(database).submit(req.request_id, req, parts, (), {})
        return req, parts

    yield enqueue
    with psycopg.connect(database) as conn:
        for scope in scopes:
            for table in ("collection_job_part", "collection_part", "collection_job"):
                conn.execute(f"DELETE FROM qtrade_data.{table} WHERE scope_id=%s", (scope,))


def claim_one(url):
    claimed = PostgresJobs(url).claim(datetime.now(UTC))
    return (
        (str(claimed.part_id), str(claimed.request.scope_id), str(claimed.job_id))
        if claimed
        else None
    )


@pytest.mark.parametrize("level", ["source", "scope", "run"])
def test_large_and_small_runs_both_progress_within_two_claims(database, queue, level):
    source, scope = "fixture." + uuid4().hex, uuid4()
    big, _ = queue(20, source=source, scope=scope)
    small, _ = queue(
        1, source=source if level != "source" else None, scope=scope if level == "run" else None
    )
    claimed = []
    for _ in range(2):
        # A new adapter instance cannot reset the persisted round-robin turn.
        repo = PostgresJobs(database)
        item = repo.claim(datetime.now(UTC))
        assert item is not None
        claimed.append(item.job_id)
        repo.complete(item, None)
    assert set(claimed) == {big.request_id, small.request_id}


def test_continuous_new_runs_join_tail_without_starving_existing_run(database, queue):
    source, scope = "fixture." + uuid4().hex, uuid4()
    big, _ = queue(20, source=source, scope=scope)
    initial = PostgresJobs(database).claim(datetime.now(UTC))
    PostgresJobs(database).complete(initial, None)
    results, newcomers = [], []
    for _ in range(8):
        small, _ = queue(1, source=source, scope=scope)
        newcomers.append(small.request_id)
        repo = PostgresJobs(database)
        item = repo.claim(datetime.now(UTC))
        assert item is not None
        results.append(item.job_id)
        repo.complete(item, None)
    assert results.count(big.request_id) >= 2
    assert newcomers[0] in results[:3]


def test_competing_processes_claim_distinct_work_and_small_scope_gets_turn(database, queue):
    source = "fixture." + uuid4().hex
    big, _ = queue(20, source=source)
    small, _ = queue(1, source=source)
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
        rows = list(pool.map(claim_one, [str(database)] * 8))
    assert all(rows) and len({row[0] for row in rows}) == 8
    assert str(small.request_id) in {row[2] for row in rows}
    assert str(big.request_id) in {row[2] for row in rows}


def test_locked_work_does_not_block_another_run(database, queue):
    source = "fixture." + uuid4().hex
    held, parts = queue(1, source=source)
    small, _ = queue(1, source=source)
    with psycopg.connect(database) as conn:
        conn.execute(
            "SELECT part_id FROM qtrade_data.collection_part "
            "WHERE scope_id=%s AND part_id=%s FOR UPDATE",
            (held.scope_id, parts[0].part_id),
        )
        repo = PostgresJobs(database)
        chosen = repo.claim(datetime.now(UTC))
        assert chosen.job_id == small.request_id
        repo.complete(chosen, None)
    assert PostgresJobs(database).claim(datetime.now(UTC)).job_id == held.request_id


def test_cursor_and_lease_rollback_together_on_claim_failure(database, queue, monkeypatch):
    from qtrade_data_postgres.dispatch import FairDispatch

    req, _ = queue(1)
    advance = FairDispatch.advance

    def broken(self, chosen):
        advance(self, chosen)
        raise RuntimeError("controlled failed claim transaction")

    monkeypatch.setattr(FairDispatch, "advance", broken)
    with pytest.raises(RuntimeError, match="controlled failed claim"):
        PostgresJobs(database).claim(datetime.now(UTC))
    with psycopg.connect(database) as conn:
        assert conn.execute(
            "SELECT state,attempt FROM qtrade_data.collection_part WHERE scope_id=%s",
            (req.scope_id,),
        ).fetchone() == ("queued", 0)
        assert conn.execute(
            "SELECT count(*) FROM qtrade_data.dispatch_cursor WHERE source=%s", (req.source,)
        ).fetchone() == (0,)
    monkeypatch.setattr(FairDispatch, "advance", advance)
    assert PostgresJobs(database).claim(datetime.now(UTC)).attempt == 1


def test_research_queue_rotates_scopes_with_many_runs(database):
    from test_research_interface import ControlledSource
    from test_research_interface import request as research_request

    from qtrade_data_core.research import ResearchService
    from qtrade_data_http.client import StaticScopeAuthorizer
    from qtrade_data_postgres.research_jobs import PostgresResearchJobs

    big, small = uuid4(), uuid4()
    try:
        for scope, count in ((big, 8), (small, 1)):
            for _ in range(count):
                req = research_request(scope_id=scope, request_id=uuid4())
                auth = StaticScopeAuthorizer({f"{req.actor_id}:{scope}": "manage"})
                plan = ResearchService(auth, (ControlledSource(),)).preview(req)
                PostgresResearchJobs(database).enqueue(plan)
        seen = []
        for _ in range(2):
            repo = PostgresResearchJobs(database)
            claim = repo.claim()
            assert claim is not None
            seen.append(claim.plan.request.scope_id)
            repo.finish(claim)
        assert set(seen) == {big, small}
    finally:
        with psycopg.connect(database) as conn:
            for scope in (big, small):
                conn.execute("DELETE FROM qtrade_data.research_job WHERE scope_id=%s", (scope,))
                conn.execute("DELETE FROM qtrade_data.research_run WHERE scope_id=%s", (scope,))
