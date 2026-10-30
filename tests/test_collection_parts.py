from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from qtrade_application.data_management import DataManagementService
from qtrade_application.ingestion import HistoricalIngestionWorker
from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.collection_coverage import plan_collection
from qtrade_domain.collection_parts import split_collection
from qtrade_domain.identity import Role, Workspace
from qtrade_domain.ingestion import CollectionRequest
from qtrade_domain.ports import PortError
from qtrade_mock.ingestion import MockIngestionRepository


def request(days):
    now = datetime(2026, 9, 1, 1, 30, tzinfo=UTC)
    return CollectionRequest(
        uuid4(),
        uuid4(),
        "mairui.a-share.tick",
        "mairui-default",
        "601288.SH",
        "tick",
        now,
        now + timedelta(days=days),
        now,
        "raw.v1",
    )


@pytest.mark.parametrize("days,count", [(1, 1), (7, 1), (7.001, 2), (9, 2), (14, 2), (365, 53)])
def test_contiguous_bounded_parts_and_stable_retry_identity(days, count):
    original = request(days)
    parts = split_collection(original)
    assert len(parts) == count
    assert parts == split_collection(original)
    assert parts[0].start == original.start and parts[-1].end == original.end
    assert all(item.end - item.start <= timedelta(days=7) for item in parts)
    assert all(left.end == right.start for left, right in zip(parts, parts[1:], strict=False))
    assert len({part.task_id for part in parts}) == count
    if count > 1:
        assert {part.download_group_id for part in parts} == {original.task_id}
        assert [part.part_index for part in parts] == list(range(1, count + 1))
        assert {part.part_count for part in parts} == {count}


def test_group_conflict_rolls_back_earlier_parts_and_retry_is_idempotent():
    original = request(9)
    actor = uuid4()
    access = WorkspaceAccess(
        Workspace(
            original.workspace_id, "Test", "personal", actor, False, False, original.created_at
        ),
        actor,
        Role.ADMIN,
    )
    repo = MockIngestionRepository(lambda *_: access)
    parts = split_collection(original)
    repo.create(actor, replace(parts[-1], instrument_id="OTHER"))
    with pytest.raises(PortError):
        repo.create_many(actor, parts)
    assert len(repo.list_tasks(original.workspace_id)) == 1
    fresh = split_collection(replace(original, task_id=uuid4()))
    first = repo.create_many(actor, fresh)
    assert repo.create_many(actor, fresh) == first


def test_missing_download_splits_only_missing_interval():
    original = request(20)
    actor = uuid4()
    access = WorkspaceAccess(
        Workspace(
            original.workspace_id, "Test", "personal", actor, False, False, original.created_at
        ),
        actor,
        Role.ADMIN,
    )
    repo = MockIngestionRepository(lambda *_: access)
    from qtrade_domain.ingestion import IngestionTask

    completed_request = replace(original, task_id=uuid4(), end=original.start + timedelta(days=7))
    completed = IngestionTask(
        completed_request,
        actor,
        "completed",
        original.created_at,
        collection_complete=True,
        batch_ids=(completed_request.batch_id(0),),
    )

    class Provider:
        source = original.source

    worker = HistoricalIngestionWorker(repo, None, None, (Provider(),))
    service = DataManagementService(repo, None, None, worker)
    parts = service.submit_missing(access, original, plan_collection(original, (completed,)))
    assert len(parts) == 2
    ordered = sorted(parts, key=lambda task: task.request.start)
    assert ordered[0].request.start == completed_request.end
    assert ordered[0].request.end == ordered[1].request.start
    assert ordered[-1].request.end == original.end
    assert all(task.request.end - task.request.start <= timedelta(days=7) for task in parts)
