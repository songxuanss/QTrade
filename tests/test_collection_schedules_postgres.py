"""Isolated persistence/competition fences, not automatic source/UI readiness."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from legacy_collection_fixture import LegacyCollectionFixture
from test_collection_schedules import draft
from test_fair_dispatch_postgres import database as isolated_database
from test_fair_dispatch_postgres import root_database as root_fixture

from qtrade_data_core.ports import DataFailure
from qtrade_data_postgres.schedules import PostgresSchedules

database, root_database = isolated_database, root_fixture


def test_schedule_persistence_scope_and_duplicate_creation(database):
    store = PostgresSchedules(database)
    value = draft()
    now = datetime.now(UTC)
    created = store.create(value, now)
    assert created.state == "disabled" and store.claim(now) is None
    assert PostgresSchedules(database).get(value.scope_id, value.schedule_id) == created
    assert store.create(value, now + timedelta(days=1)) == created
    with pytest.raises(DataFailure, match="conflict"):
        store.create(value.model_copy(update={"name": "changed"}), now)
    with pytest.raises(DataFailure, match="not_found"):
        store.get(uuid4(), value.schedule_id)


def test_competing_claims_expiry_disable_and_repeat_enable_fences(database):
    store = PostgresSchedules(database)
    value = draft()
    now = datetime.now(UTC)
    store.create(value, now)
    enabled = store.control(value.scope_id, value.schedule_id, value.revision, "enabled")
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: PostgresSchedules(database).claim(now), range(4)))
    claims = [c for c in claims if c]
    assert len(claims) == 1
    first = claims[0]
    assert store.control(value.scope_id, value.schedule_id, value.revision, "enabled") == enabled
    assert store.claim(now) is None  # Repeat enable cannot revoke ownership.
    with psycopg.connect(database) as conn:
        conn.execute(
            "UPDATE qtrade_data.collection_schedule SET lease_until=now()-interval '1 second' "
            "WHERE scope_id=%s",
            (value.scope_id,),
        )
    with pytest.raises(DataFailure, match="conflict"):
        store.advance(first, now + timedelta(days=1), now)
    second = store.claim(now)
    assert second.token != first.token
    with pytest.raises(DataFailure, match="conflict"):
        store.advance(first, now + timedelta(days=1), now)
    store.control(value.scope_id, value.schedule_id, value.revision, "disabled")
    with pytest.raises(DataFailure, match="conflict"):
        store.advance(second, now + timedelta(days=1), now)
    assert store.claim(now) is None


def test_advancement_and_sql_null_constraints(database):
    store = PostgresSchedules(database)
    value = draft()
    now = datetime.now(UTC)
    store.create(value, now)
    store.control(value.scope_id, value.schedule_id, value.revision, "enabled")
    lease = store.claim(now)
    with pytest.raises(DataFailure, match="invalid_input"):
        store.advance(lease, now, now)
    store.advance(lease, now + timedelta(days=1), now)
    assert store.claim(now) is None
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE qtrade_data.collection_schedule SET state='paused',pause_reason=NULL "
                "WHERE scope_id=%s",
                (value.scope_id,),
            )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE qtrade_data.collection_schedule SET draft='{}'::jsonb WHERE scope_id=%s",
                (value.scope_id,),
            )


def test_recovered_slot_reservation_is_unique_and_immutable(database):
    store = PostgresSchedules(database)
    value = draft()
    now = datetime.now(UTC)
    store.create(value, now)
    store.control(value.scope_id, value.schedule_id, value.revision, "enabled")
    first = store.claim(now)
    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(
            pool.map(lambda _: PostgresSchedules(database).reserve_run(first, now), range(3))
        )
    assert len(set(runs)) == 1
    with psycopg.connect(database) as conn:
        conn.execute(
            "UPDATE qtrade_data.collection_schedule SET lease_until=now()-interval '1 second' "
            "WHERE scope_id=%s",
            (value.scope_id,),
        )
    with pytest.raises(DataFailure, match="conflict"):
        store.reserve_run(first, now)
    second = store.claim(now)
    assert store.reserve_run(second, now) == runs[0]
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.collection_schedule_run WHERE scope_id=%s",
                (value.scope_id,),
            ).fetchone()[0]
            == 1
        )
        with pytest.raises(psycopg.errors.RaiseException), conn.transaction():
            conn.execute(
                "DELETE FROM qtrade_data.collection_schedule_run WHERE scope_id=%s",
                (value.scope_id,),
            )
    store.advance(second, now + timedelta(days=1), now)
    assert store.claim(now) is None


def test_dispatch_rechecks_frozen_actor_and_pauses_before_reservation(database):
    from qtrade_data_core.schedules import ScheduleControls
    from qtrade_data_http.client import StaticScopeAuthorizer

    store = PostgresSchedules(database)
    value = draft()
    now = datetime.now(UTC)
    store.create(value, now)
    store.control(value.scope_id, value.schedule_id, value.revision, "enabled")
    lease = store.claim(now)
    auth = StaticScopeAuthorizer({f"{value.actor_id}:{value.scope_id}": "manage"})
    controls = ScheduleControls(auth, store)
    auth.grants.clear()  # Revoked after enable/claim, before any dispatch identity.
    with pytest.raises(DataFailure, match="forbidden"):
        controls.reserve_authorized_run(lease, now)
    paused = store.get(value.scope_id, value.schedule_id)
    assert paused.state == "paused" and paused.pause_reason == "authorization"
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.collection_schedule_run WHERE scope_id=%s",
                (value.scope_id,),
            ).fetchone()[0]
            == 0
        )
    with pytest.raises(DataFailure, match="conflict"):
        store.reserve_run(lease, now)
    auth.grants[f"{value.actor_id}:{value.scope_id}"] = "manage"
    store.control(value.scope_id, value.schedule_id, value.revision, "enabled")
    renewed = store.claim(now)
    assert controls.reserve_authorized_run(renewed, now)


def scheduled_collection(database, tmp_path, *, freeze=True, due_at=None):
    """Controlled provider, real PG admission and ordinary archive/Worker."""
    from test_independent_collection import bar

    from qtrade_data_contracts.collection import CollectionJobRequest
    from qtrade_data_contracts.schedule_dispatch import (
        MarketDispatchInput,
        ScheduleDispatchDecision,
    )
    from qtrade_data_core.collection import CollectionResult, CollectionService
    from qtrade_data_files.archive import FileCollectionArchive
    from qtrade_data_http.client import StaticScopeAuthorizer
    from qtrade_data_mairui.collection import MaiRuiCollection
    from qtrade_data_postgres.scheduled_jobs import PostgresScheduledJobs

    value = draft()
    now = datetime.now(UTC)
    store = PostgresSchedules(database)
    store.create(value, due_at or now)
    store.control(value.scope_id, value.schedule_id, value.revision, "enabled")
    lease = store.claim(now)
    run = store.reserve_run(lease, now)
    req = CollectionJobRequest(
        schema_version=1,
        scope_id=value.scope_id,
        request_id=run,
        correlation_id=run,
        actor_id=value.actor_id,
        source=value.selection.source,
        dataset_kind="bars",
        instrument_ids=value.selection.instrument_ids,
        granularity="1d",
        start=datetime(2026, 7, 1, tzinfo=UTC),
        end=datetime(2026, 7, 2, tzinfo=UTC),
    )
    frozen_decision = ScheduleDispatchDecision(
        schema_version=1,
        scope_id=value.scope_id,
        schedule_id=value.schedule_id,
        run_id=run,
        revision=value.revision,
        due_at=lease.record.next_due_at,
        policy_version="fixture.window.v1",
        basis="controlled explicit window",
        dispatch=MarketDispatchInput(kind="market", request=req),
        frozen_at=now,
    )
    if freeze:
        store.freeze_dispatch(lease, frozen_decision, now)
    provider = MaiRuiCollection("fixture-key", pro=True, cache=tmp_path / "cache")
    provider.names = lambda ids: dict.fromkeys(ids, "农业银行")

    def fetch(part, *, capture):
        capture("history", [{"c": "3.25"}])
        return CollectionResult(
            ({"c": "3.25"},),
            (bar().model_copy(update={"timestamp": part.start, "granularity": "1d"}),),
            {"volume": "shares", "prices": "CNY", "value": "CNY"},
            "UTC",
            {},
        )

    # Controlled replacement implements the legacy fetch seam only.
    provider = LegacyCollectionFixture(provider)
    provider.fetch = fetch
    service = CollectionService(
        StaticScopeAuthorizer({f"{value.actor_id}:{value.scope_id}": "manage"}),
        PostgresScheduledJobs(database, lease, run),
        FileCollectionArchive(tmp_path / "archives"),
        (provider,),
    )
    return store, lease, req, service


def test_scheduled_admission_uses_existing_worker_and_is_idempotent(database, tmp_path):
    from qtrade_data_postgres.jobs import PostgresJobs

    store, lease, req, service = scheduled_collection(database, tmp_path)
    preview = service.preview(req)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: service.submit(req, preview.coverage_version), range(3)))
    assert {item["job_id"] for item in results} == {str(req.request_id)}
    assert service.run_once() is not None
    persisted = PostgresJobs(database).job(req.scope_id, req.request_id)
    assert persisted["state"] == "completed"
    assert persisted["parts"][0]["publication"]["record_count"] == 1
    store.control(req.scope_id, lease.record.draft.schedule_id, lease.record.revision, "disabled")
    assert service.submit(req, preview.coverage_version) == persisted
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.collection_job WHERE scope_id=%s", (req.scope_id,)
            ).fetchone()[0]
            == 1
        )


def test_scheduled_admission_rejects_unfrozen_selection_or_identity(database, tmp_path):
    from qtrade_data_postgres.jobs import PostgresJobs

    _, _, req, service = scheduled_collection(database, tmp_path)
    for changes in (
        {"mode": "all"},
        {"request_id": uuid4()},
        {"instrument_ids": ("600000.SH",)},
        {"source": "mairui.pro.bars", "granularity": "5m"},
    ):
        changed = req.model_copy(update=changes)
        preview = service.preview(changed)
        with pytest.raises(DataFailure, match="conflict"):
            service.submit(changed, preview.coverage_version)
        assert PostgresJobs(database).find(req.scope_id, changed.request_id) is None


def test_frozen_dispatch_survives_reopen_reclaim_and_rejects_moved_window(database, tmp_path):
    store, lease, req, _ = scheduled_collection(database, tmp_path)
    frozen = store.get_dispatch(req.scope_id, req.request_id)
    later = datetime.now(UTC) + timedelta(seconds=1)
    repeated = frozen.model_copy(update={"frozen_at": later})
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: store.freeze_dispatch(lease, repeated, later), range(3)))
    assert results == [frozen] * 3  # First freeze time and digest stay unchanged.
    changed = frozen.model_copy(
        update={
            "dispatch": frozen.dispatch.model_copy(
                update={"request": req.model_copy(update={"end": req.end + timedelta(days=1)})}
            )
        }
    )
    with pytest.raises(DataFailure, match="conflict"):
        store.freeze_dispatch(lease, changed, later)
    with pytest.raises(DataFailure, match="not_found"):
        store.get_dispatch(uuid4(), req.request_id)
    with psycopg.connect(database) as conn:
        conn.execute(
            "UPDATE qtrade_data.collection_schedule SET lease_until=clock_timestamp()-"
            "interval '1 second' WHERE scope_id=%s",
            (req.scope_id,),
        )
    with pytest.raises(DataFailure, match="conflict"):
        store.freeze_dispatch(lease, frozen, datetime.now(UTC))
    current = store.claim(datetime.now(UTC))
    assert store.reserve_run(current, datetime.now(UTC)) == req.request_id
    assert store.freeze_dispatch(current, repeated, datetime.now(UTC)) == frozen
    assert PostgresSchedules(database).get_dispatch(req.scope_id, req.request_id) == frozen


def test_dispatch_sql_immutable_and_missing_json_identity_rejected(database, tmp_path):
    _, _, req, _ = scheduled_collection(database, tmp_path)
    with psycopg.connect(database) as conn:
        for statement in (
            "UPDATE qtrade_data.collection_schedule_dispatch SET content_sha256=repeat('b',64)",
            "DELETE FROM qtrade_data.collection_schedule_dispatch",
            "TRUNCATE qtrade_data.collection_schedule_dispatch",
        ):
            with pytest.raises(psycopg.errors.RaiseException), conn.transaction():
                conn.execute(statement)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO qtrade_data.collection_schedule_dispatch "
                "(scope_id,run_id,payload,content_sha256) VALUES(%s,%s,'{}',repeat('a',64))",
                (req.scope_id, uuid4()),
            )


def test_changed_range_cannot_be_admitted_against_frozen_dispatch(database, tmp_path):
    from qtrade_data_postgres.jobs import PostgresJobs

    _, _, req, service = scheduled_collection(database, tmp_path)
    changed = req.model_copy(update={"end": req.end + timedelta(days=1)})
    preview = service.preview(changed)
    with pytest.raises(DataFailure, match="conflict"):
        service.submit(changed, preview.coverage_version)
    assert PostgresJobs(database).find(req.scope_id, req.request_id) is None


@pytest.mark.parametrize("corruption", ["missing", "digest", "slot"])
def test_dispatch_missing_or_corrupt_evidence_fails_closed(database, tmp_path, corruption):
    from test_schedule_dispatch import decision

    from qtrade_data_postgres.jobs import PostgresJobs

    store, lease, req, service = scheduled_collection(database, tmp_path, freeze=False)
    if corruption != "missing":
        frozen = decision(lease.record.draft).model_copy(
            update={
                "run_id": req.request_id,
                "due_at": lease.record.next_due_at,
                "dispatch": decision(lease.record.draft).dispatch.model_copy(
                    update={"request": req}
                ),
            }
        )
        if corruption == "slot":
            frozen = frozen.model_copy(update={"revision": "b" * 64})
        with psycopg.connect(database) as conn:
            # Controlled corrupt archive evidence, never a production mutation.
            conn.execute(
                "INSERT INTO qtrade_data.collection_schedule_dispatch "
                "(scope_id,run_id,payload,content_sha256) VALUES(%s,%s,%s::jsonb,%s)",
                (
                    req.scope_id,
                    req.request_id,
                    frozen.model_dump_json(),
                    "a" * 64 if corruption == "digest" else frozen.content_sha256,
                ),
            )
        with pytest.raises(DataFailure, match="unverified"):
            store.get_dispatch(req.scope_id, req.request_id)
    else:
        with pytest.raises(DataFailure, match="not_found"):
            store.get_dispatch(req.scope_id, req.request_id)
    preview = service.preview(req)
    with pytest.raises(DataFailure, match="unverified|conflict"):
        service.submit(req, preview.coverage_version)
    assert PostgresJobs(database).find(req.scope_id, req.request_id) is None


@pytest.mark.parametrize("confirmed", [False, True])
def test_current_credit_estimate_is_rechecked_before_automatic_admission(
    database, tmp_path, confirmed
):
    from qtrade_data_core.schedule_approval import (
        ScheduleApprovalRequired,
        ScheduleExecutionApproval,
    )
    from qtrade_data_postgres.jobs import PostgresJobs

    _, lease, req, service = scheduled_collection(database, tmp_path)
    preview = service.preview(req)
    approval = ScheduleExecutionApproval(lease.record.draft)
    original_estimate = service.providers[0].estimate
    service.providers[0].estimate = lambda request, count: original_estimate(
        request, count
    ).model_copy(update={"credits_max": 501})
    # Coverage may stay identical while the source estimate changes. Frozen
    # approval must still reject the rebuilt current cost before inserting.
    with pytest.raises(ScheduleApprovalRequired):
        service.submit(
            req, preview.coverage_version, confirmed=confirmed, automatic_approval=approval
        )
    assert PostgresJobs(database).find(req.scope_id, req.request_id) is None
    service.providers[0].estimate = original_estimate
    service.plan_guard = service.plan_guard.model_copy(update={"confirm_new_parts": 1})
    current = service.preview(req)
    assert current.confirmation_required
    accepted = service.submit(req, current.coverage_version, automatic_approval=approval)
    assert accepted["job_id"] == str(req.request_id)


def test_scheduled_admission_rechecks_expiry_after_waiting_for_row_lock(database, tmp_path):
    from time import monotonic, sleep

    from qtrade_data_postgres.jobs import PostgresJobs

    _, _, req, service = scheduled_collection(database, tmp_path)
    preview = service.preview(req)
    with psycopg.connect(database) as conn:
        conn.execute(
            "UPDATE qtrade_data.collection_schedule SET "
            "lease_until=clock_timestamp()+interval '2 seconds' WHERE scope_id=%s",
            (req.scope_id,),
        )
    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(database) as holder:
            holder.execute(
                "SELECT 1 FROM qtrade_data.collection_schedule WHERE scope_id=%s FOR UPDATE",
                (req.scope_id,),
            )
            submitted = pool.submit(service.submit, req, preview.coverage_version)
            deadline = monotonic() + 1.5
            with psycopg.connect(database, autocommit=True) as observer:
                while monotonic() < deadline:
                    waiting = observer.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND %s=ANY(pg_blocking_pids(pid))",
                        (holder.info.backend_pid,),
                    ).fetchone()[0]
                    if waiting:
                        break
                    sleep(0.01)
                assert waiting, "submission must actually wait on the unchanged schedule row"
            holder.execute("SELECT pg_sleep(2.1)")
        with pytest.raises(DataFailure, match="conflict"):
            submitted.result(timeout=5)
    assert PostgresJobs(database).find(req.scope_id, req.request_id) is None


@pytest.mark.parametrize("change", ["disable", "expire", "reclaim", "elapsed"])
def test_scheduled_admission_fences_control_during_preplanning(database, tmp_path, change):
    from qtrade_data_postgres.jobs import PostgresJobs

    store, lease, req, service = scheduled_collection(database, tmp_path)
    preview = service.preview(req)
    original_names = service.providers[0].names
    recovered = []

    def names(ids):
        # Mutation occurs inside submission's build, after its transaction began.
        if change == "disable":
            store.control(
                req.scope_id, lease.record.draft.schedule_id, lease.record.revision, "disabled"
            )
        else:
            with psycopg.connect(database) as conn:
                conn.execute(
                    "UPDATE qtrade_data.collection_schedule SET "
                    "lease_until=clock_timestamp()+%s::interval WHERE scope_id=%s",
                    ("50 milliseconds" if change == "elapsed" else "-1 second", req.scope_id),
                )
                if change == "elapsed":
                    conn.commit()
                    # Real database time moves beyond the lease while the job
                    # transaction's now() is still its preplanning start time.
                    conn.execute("SELECT pg_sleep(0.1)")
            if change == "reclaim":
                recovered.append(store.claim(datetime.now(UTC)))
        return original_names(ids)

    service.providers[0].names = names
    with pytest.raises(DataFailure, match="conflict"):
        service.submit(req, preview.coverage_version)
    assert PostgresJobs(database).find(req.scope_id, req.request_id) is None
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.collection_part WHERE scope_id=%s",
                (req.scope_id,),
            ).fetchone()[0]
            == 0
        )
    if recovered:
        from qtrade_data_postgres.scheduled_jobs import PostgresScheduledJobs

        current = recovered[0]
        assert current.token != lease.token
        assert store.reserve_run(current, datetime.now(UTC)) == req.request_id
        service.providers[0].names = original_names
        service.repository = PostgresScheduledJobs(database, current, req.request_id)
        assert service.submit(req, preview.coverage_version)["job_id"] == str(req.request_id)
