"""Actual PG orchestration/recovery with controlled provider, not live timer/UI."""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from test_collection_schedules import draft
from test_collection_schedules_postgres import scheduled_collection
from test_fair_dispatch_postgres import database as isolated_database
from test_fair_dispatch_postgres import root_database as root_fixture
from test_schedule_dispatch import decision

from qtrade_data_core.collection import CollectionService
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.schedule_clock import previous_slot
from qtrade_data_core.schedule_runner import ScheduleRunner
from qtrade_data_postgres.jobs import PostgresJobs
from qtrade_data_postgres.scheduled_jobs import PostgresScheduledJobs
from qtrade_data_postgres.schedules import PostgresSchedules

database, root_database = isolated_database, root_fixture


class PolicyFixture:
    def __init__(self, result="input"):
        self.calls, self.result = [], result

    def prepare(self, value, run, due, now):
        self.calls.append(run)
        if self.result == "skip":
            return None
        if self.result == "unsupported":
            raise DataFailure("unsupported")
        frozen = decision(value)
        request = frozen.dispatch.request.model_copy(
            update={
                "request_id": run,
                "correlation_id": run,
            }
        )
        return frozen.model_copy(
            update={
                "run_id": run,
                "due_at": due,
                "frozen_at": now,
                "dispatch": frozen.dispatch.model_copy(update={"request": request}),
            }
        )


def runtime(database, tmp_path, result="input", old=False):
    now = datetime.now(UTC)
    due = previous_slot(draft(), now) - (timedelta(days=10000) if old else timedelta(0))
    store, lease, req, service = scheduled_collection(
        database,
        tmp_path,
        freeze=False,
        due_at=due,
    )
    with psycopg.connect(database) as conn:
        conn.execute(
            "UPDATE qtrade_data.collection_schedule SET lease_until=clock_timestamp()-"
            "interval '1 second' WHERE scope_id=%s",
            (req.scope_id,),
        )
    policy = PolicyFixture(result)

    def factory(owner, run):
        return CollectionService(
            service.auth,
            PostgresScheduledJobs(database, owner, run),
            service.archive,
            service.providers,
            service.plan_guard,
        )

    return store, lease, req, service, policy, factory


def test_runtime_admits_then_ordinary_worker_publishes_nonempty(database, tmp_path):
    store, lease, req, service, policy, factory = runtime(database, tmp_path)
    runner = ScheduleRunner(service.auth, store, policy, factory)
    tick = runner.run_once()
    assert tick.state == "submitted" and tick.run_id == req.request_id
    queued = PostgresJobs(database).job(req.scope_id, req.request_id)
    assert queued["counts"]["queued"] == 1  # Submission alone is not completion.
    assert runner.run_once() is None and len(policy.calls) == 1
    assert service.run_once() is not None
    published = PostgresJobs(database).job(req.scope_id, req.request_id)
    assert published["state"] == "completed"
    assert published["parts"][0]["publication"]["record_count"] == 1
    frozen = store.get_dispatch(req.scope_id, req.request_id)
    assert frozen.dispatch.request.start == req.start
    assert store.get(req.scope_id, lease.record.draft.schedule_id).state == "enabled"


def test_recovery_after_enqueue_before_cursor_advance_reuses_original_request(database, tmp_path):
    store, lease, req, service, policy, factory = runtime(database, tmp_path)

    def interrupted(*args):
        raise DataFailure("conflict")

    store.advance = interrupted
    assert ScheduleRunner(service.auth, store, policy, factory).run_once().state == "deferred"
    frozen = store.get_dispatch(req.scope_id, req.request_id)
    with psycopg.connect(database) as conn:
        conn.execute(
            "UPDATE qtrade_data.collection_schedule SET lease_until=clock_timestamp()-"
            "interval '1 second' WHERE scope_id=%s",
            (req.scope_id,),
        )
    recovered = PostgresSchedules(database)
    tick = ScheduleRunner(service.auth, recovered, policy, factory).run_once()
    assert tick.state == "submitted" and tick.run_id == req.request_id
    assert len(policy.calls) == 1  # Recovery never recalculates the date window.
    assert recovered.get_dispatch(req.scope_id, req.request_id) == frozen
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM qtrade_data.collection_job WHERE scope_id=%s", (req.scope_id,)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "result,state,reason",
    [
        ("skip", "calendar_skipped", None),
        ("unsupported", "paused", "source_unavailable"),
        ("revoked", "paused", "authorization"),
        ("old", "window_skipped", None),
    ],
)
def test_calendar_rejection_and_outage_paths_do_not_create_downloads(
    database, tmp_path, result, state, reason
):
    store, lease, req, service, policy, factory = runtime(
        database,
        tmp_path,
        result,
        old=result == "old",
    )
    if result == "revoked":
        service.auth.grants.clear()
    tick = ScheduleRunner(service.auth, store, policy, factory).run_once()
    assert tick.state == state
    current = store.get(req.scope_id, lease.record.draft.schedule_id)
    assert current.pause_reason == reason
    assert PostgresJobs(database).find(req.scope_id, req.request_id) is None
    if result in {"revoked", "old"}:
        assert policy.calls == []
