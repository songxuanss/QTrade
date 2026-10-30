"""Business ranges, aliases, resumable checkpoints and duplicate races."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.collection_coverage import plan_collection
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ingestion import CollectionRequest, IngestionTask
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_mock.ingestion import MockIngestionRepository

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def request(start=0, end=6):
    return CollectionRequest(
        uuid4(),
        uuid4(),
        "mairui.a-share.tick",
        "mairui-default",
        "600519.SH",
        "tick",
        NOW + timedelta(hours=start),
        NOW + timedelta(hours=end),
        NOW,
        "raw-tick.v1",
    )


def task(req, start, end, status="completed", **changes):
    other = replace(
        req, task_id=uuid4(), start=NOW + timedelta(hours=start), end=NOW + timedelta(hours=end)
    )
    fields = (
        {"collection_complete": True, "batch_ids": (other.batch_id(0),)}
        if status == "completed"
        else {}
    )
    fields.update(changes)
    return IngestionTask(other, uuid4(), status, NOW, **fields)


def test_union_and_disjoint_missing_ranges():
    req = request()
    tasks = (task(req, 0, 1), task(req, 1, 2), task(req, 3, 4), task(req, 3, 5))
    plan = plan_collection(req, tasks)
    assert plan.status == "partial"
    assert plan.missing == (
        (NOW + timedelta(hours=2), NOW + timedelta(hours=3)),
        (NOW + timedelta(hours=5), NOW + timedelta(hours=6)),
    )
    assert plan.submit_ranges == plan.missing


def test_full_coverage_and_symbol_aliases():
    req = request()
    done = task(req, -1, 7)
    for name in ("600519", "CN.600519", "600519.sh"):
        alias = replace(done, request=replace(done.request, instrument_id=name))
        plan = plan_collection(req, (alias,))
        assert plan.status == "complete" and not plan.missing and not plan.submit_ranges


def test_scope_source_authorization_instrument_and_touching_edges_do_not_cover():
    req = request()
    done = task(req, 0, 6)
    others = tuple(
        replace(done, request=replace(done.request, **change))
        for change in (
            {"workspace_id": uuid4()},
            {"source": "another"},
            {"authorization_id": "another"},
            {"instrument_id": "600000.SH"},
            {"start": NOW + timedelta(hours=6), "end": NOW + timedelta(hours=7)},
        )
    )
    plan = plan_collection(req, others)
    assert plan.status == "new" and not plan.overlapping
    assert plan.missing == ((req.start, req.end),)


def test_partial_failed_task_reuses_checkpoint_without_certifying_complete_coverage():
    req = request()
    failed = task(req, 0, 6, "failed", cursor="0:1000")
    failed = replace(failed, batch_ids=(failed.request.batch_id(0),))
    plan = plan_collection(req, (failed,))
    assert plan.status == "partial" and plan.missing == ((req.start, req.end),)
    assert plan.reuse == (failed,) and not plan.submit_ranges
    assert plan.reuse[0].cursor == "0:1000"


def test_active_task_is_reused_and_missing_extension_is_created():
    req = request()
    active = task(req, 0, 3, "running")
    plan = plan_collection(req, (active,))
    assert plan.reuse == (active,)
    assert plan.submit_ranges == ((NOW + timedelta(hours=3), req.end),)


def test_failed_superset_is_not_resumed_outside_user_window():
    req = request()
    failed = task(req, -1, 7, "failed")
    plan = plan_collection(req, (failed,))
    assert not plan.reuse and plan.submit_ranges == ((req.start, req.end),)


def test_duplicate_create_race_rejects_one_and_explicit_redownload_is_allowed():
    req = request()
    workspace = Workspace(req.workspace_id, "Coverage", "personal", None, False, False, NOW)
    actor = uuid4()
    access = WorkspaceAccess(workspace, actor, Role.ADMIN)
    repo = MockIngestionRepository(
        lambda uid, wid: access if uid == actor and wid == req.workspace_id else None
    )

    def create(_):
        try:
            return repo.create(actor, replace(req, task_id=uuid4()), avoid_overlap=True)
        except PortError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(create, range(2)))
    assert results.count(ErrorCode.CONFLICT) == 1
    assert len(repo.overlapping_tasks(req)) == 1
    repo.create(actor, replace(req, task_id=uuid4()))
    assert len(repo.overlapping_tasks(req)) == 2
