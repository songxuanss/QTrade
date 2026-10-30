"""Real independent PostgreSQL schema tests; use scripts/selftest-data.py."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from qtrade_data_contracts import ReferenceIdentity, UniverseContent, UniverseMember
from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.migrate import apply
from qtrade_data_postgres.universes import PostgresUniverses

URL = os.environ.get("QTRADE_TEST_DATA_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requires explicit independent data test database")


@pytest.fixture
def store():
    assert URL
    apply(URL)
    return PostgresUniverses(URL)


def content(scope):
    return UniverseContent(
        schema_version=1,
        scope_id=scope,
        universe_id="custom:test",
        version=1,
        name="test",
        category="custom",
        source="user_selection",
        observed_at=datetime.now(UTC),
        membership_basis="current_snapshot",
        market_scope=("SH",),
        classification_version="user-selection.v1",
        members=(
            UniverseMember(
                instrument=ReferenceIdentity(
                    instrument_id="601288.SH",
                    instrument_kind="stock",
                    name="农业银行",
                    exchange="SH",
                )
            ),
        ),
    )


def test_independent_schema_idempotence_and_no_business_foreign_keys(store):
    assert URL
    assert apply(URL) == ()
    with psycopg.connect(URL) as conn:
        assert conn.execute("SELECT to_regclass('workspace')").fetchone()[0] is None
        foreign = conn.execute("""SELECT count(*) FROM pg_constraint
            WHERE connamespace='qtrade_data'::regnamespace AND contype='f'
            AND confrelid NOT IN (SELECT oid FROM pg_class
                WHERE relnamespace='qtrade_data'::regnamespace)""").fetchone()[0]
        assert foreign == 0


def test_snapshots_are_immutable_versioned_and_scoped(store):
    scope, actor = uuid4(), uuid4()
    original = content(scope)
    one = store.append(actor, original)
    changed = UniverseContent.model_validate(
        {
            **original.model_dump(),
            "observed_at": original.observed_at + timedelta(seconds=1),
            "members": (
                *original.members,
                UniverseMember(
                    instrument=ReferenceIdentity(
                        instrument_id="600519.SH",
                        instrument_kind="stock",
                        name="贵州茅台",
                        exchange="SH",
                    ),
                    affiliations=("industry:beverages", "theme:consumer"),
                ),
            ),
        }
    )
    two = store.append(actor, changed)
    assert (one.version, two.version) == (1, 2)
    assert one.snapshot_id != two.snapshot_id
    assert store.get(scope, one.snapshot_id) == one
    assert len(one.members) == 1 and len(two.members) == 2
    assert one.content_sha256 != two.content_sha256
    assert two.members[1].affiliations == ("industry:beverages", "theme:consumer")
    with pytest.raises(DataFailure, match="not_found"):
        store.get(uuid4(), one.snapshot_id)
    page = store.list(scope, limit=1)
    assert len(page) == 1
    remainder = store.list(scope, limit=1, before=(page[0].observed_at, page[0].snapshot_id))
    assert len(remainder) == 1 and remainder[0].snapshot_id != page[0].snapshot_id
    assert URL
    with psycopg.connect(URL) as conn:
        with pytest.raises(psycopg.Error, match="immutable"):
            conn.execute(
                "UPDATE qtrade_data.universe_snapshot SET version=999 WHERE snapshot_id=%s",
                (one.snapshot_id,),
            )


def test_concurrent_versions_allocate_once_and_preserve_content(store):
    scope, actor = uuid4(), uuid4()
    with ThreadPoolExecutor(max_workers=4) as executor:
        snapshots = list(executor.map(lambda _: store.append(actor, content(scope)), range(4)))
    assert sorted(s.version for s in snapshots) == [1, 2, 3, 4]
    assert len({s.snapshot_id for s in snapshots}) == 4
    assert all(store.get(scope, s.snapshot_id) == s for s in snapshots)


def test_rls_has_no_ambient_access_and_scope_limits_reads(store):
    assert URL
    a, b = uuid4(), uuid4()
    store.append(uuid4(), content(a))
    store.append(uuid4(), content(b))
    role = "data_read_" + uuid4().hex
    with psycopg.connect(URL) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA qtrade_data TO {}").format(sql.Identifier(role))
        )
        conn.execute(
            sql.SQL("GRANT SELECT ON qtrade_data.universe_snapshot TO {}").format(
                sql.Identifier(role)
            )
        )
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        assert not conn.execute("SELECT scope_id FROM qtrade_data.universe_snapshot").fetchall()
        conn.execute("SELECT set_config('qtrade_data.scope_id',%s,true)", (str(a),))
        assert conn.execute("SELECT scope_id FROM qtrade_data.universe_snapshot").fetchall() == [
            (a,)
        ]
        conn.execute("RESET ROLE")
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_campaign_freeze_dedup_seven_day_expansion_restart_and_idempotence(store):
    from qtrade_data_contracts import CollectionSelection
    from qtrade_data_core.campaigns import CollectionGateway, Coverage, work_at
    from qtrade_data_postgres.campaigns import PostgresCampaigns

    assert URL
    scope, actor = uuid4(), uuid4()
    first = store.append(actor, content(scope))
    second = store.append(
        actor,
        UniverseContent.model_validate(
            {
                **content(scope).model_dump(),
                "universe_id": "custom:overlap",
                "members": first.members,
            }
        ),
    )

    class Auth:
        allowed = True

        def require(self, *_args):
            if not self.allowed:
                raise DataFailure("forbidden")

    class Executor:
        fingerprint = "initial"
        requests = {}
        state = "viewable"

        def coverage(self, selection, instrument):
            return Coverage(
                (),
                (CollectionRange(start=selection.start, end=selection.end),),
                (),
                (),
                self.fingerprint,
            )

        def submit(self, selection, work, created_at):
            self.requests[work.task_id] = work

        def result(self, selection, task_id):
            return {"state": self.state, "raw_records": 1 if self.state == "viewable" else 0}

        def retry(self, selection, task_id):
            pass

    from qtrade_data_contracts import CollectionRange

    auth, executor = Auth(), Executor()
    repository = PostgresCampaigns(URL)
    gateway = CollectionGateway(auth, store, repository, executor)
    now = datetime.now(UTC)
    selection = CollectionSelection(
        schema_version=1,
        scope_id=scope,
        request_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        snapshot_ids=(first.snapshot_id, second.snapshot_id),
        source="fixture",
        authorization_id="fixture",
        granularity="1d",
        start=now - timedelta(days=15),
        end=now,
        transformation_version="fixture.v1",
    )
    plan = gateway.preview(selection)
    assert len(plan.members) == 1 and plan.planned_parts == 3
    assert set(plan.members[0].affiliations) == {"custom:test", "custom:overlap"}
    assert repository.expanded(scope, uuid4()) == 0
    executor.fingerprint = "changed"
    with pytest.raises(DataFailure, match="conflict"):
        gateway.submit(actor, scope, plan.plan_id, plan.coverage_version)
    assert repository.runs(scope, 20) == []
    executor.fingerprint = "initial"
    campaign = gateway.submit(actor, scope, plan.plan_id, plan.coverage_version)
    assert gateway.submit(actor, scope, plan.plan_id, plan.coverage_version) == campaign
    gateway.process(scope, campaign, page_size=1)
    assert repository.expanded(scope, campaign) == 1
    assert repository.run(scope, campaign)["state"] == "running"
    # New service instance resumes durable expansion; no duplicate task identities.
    restarted = CollectionGateway(auth, store, PostgresCampaigns(URL), executor)
    restarted.process(scope, campaign, page_size=1)
    auth.allowed = False
    with pytest.raises(DataFailure, match="forbidden"):
        restarted.process(scope, campaign)
    assert repository.expanded(scope, campaign) == 2
    auth.allowed = True
    restarted.process(scope, campaign)
    final = repository.run(scope, campaign)
    assert final["state"] == "completed" and final["viewable_members"] == 1
    assert len(executor.requests) == 3
    assert all(work.end - work.start <= timedelta(days=7) for work in executor.requests.values())
    assert [work_at(plan, campaign, i).task_id for i in range(3)] == list(executor.requests)
    with pytest.raises(DataFailure, match="not_found"):
        gateway.run(actor, uuid4(), campaign)


def test_campaign_empty_results_are_not_success_and_failure_retry_preserves_success(store):
    from qtrade_data_contracts import CollectionRange, CollectionSelection
    from qtrade_data_core.campaigns import CollectionGateway, Coverage
    from qtrade_data_postgres.campaigns import PostgresCampaigns

    assert URL
    scope, actor = uuid4(), uuid4()
    snapshot = store.append(actor, content(scope))

    class Auth:
        def require(self, *args):
            pass

    class Executor:
        state = "no_observation"
        retried = []

        def coverage(self, selection, instrument):
            return Coverage(
                (), (CollectionRange(start=selection.start, end=selection.end),), (), (), "stable"
            )

        def submit(self, *args):
            pass

        def result(self, *args):
            return {"state": self.state, "raw_records": 0}

        def retry(self, selection, task_id):
            self.retried.append(task_id)

    executor = Executor()
    repo = PostgresCampaigns(URL)
    gateway = CollectionGateway(Auth(), store, repo, executor)
    now = datetime.now(UTC)
    selection = CollectionSelection(
        schema_version=1,
        scope_id=scope,
        actor_id=actor,
        request_id=uuid4(),
        correlation_id=uuid4(),
        snapshot_ids=(snapshot.snapshot_id,),
        source="fixture",
        authorization_id="fixture",
        granularity="1d",
        start=now - timedelta(days=8),
        end=now,
        transformation_version="fixture.v1",
    )
    plan = gateway.preview(selection)
    campaign = gateway.submit(actor, scope, plan.plan_id, plan.coverage_version)
    gateway.process(scope, campaign)
    run = repo.run(scope, campaign)
    assert run["state"] == "partial" and run["no_observation_members"] == 1
    assert run["viewable_members"] == 0 and run["collected_parts"] == 0
    executor.state = "failed"
    plan2 = gateway.preview(selection.model_copy(update={"request_id": uuid4()}))
    campaign2 = gateway.submit(actor, scope, plan2.plan_id, plan2.coverage_version)
    gateway.process(scope, campaign2)
    assert repo.run(scope, campaign2)["failed_parts"] == 2
    gateway.control(actor, scope, campaign2, "retry")
    assert len(executor.retried) == 2
    executor.state = "viewable"
    gateway.process(scope, campaign2)
    assert repo.run(scope, campaign2)["state"] == "completed"
    gateway.control(actor, scope, campaign2, "retry")
    assert len(executor.retried) == 2
