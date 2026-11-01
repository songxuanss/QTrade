"""Publish a verified completed download as an immutable research dataset."""

from uuid import UUID, uuid5

from qtrade_application.datasets import DatasetReplayService
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.datasets import PublishedDataset, ReplayRequest
from qtrade_domain.ingestion import IngestionRepositoryPort
from qtrade_domain.ports import ErrorCode, PortError


class ResearchDataService:
    def __init__(self, tasks: IngestionRepositoryPort, replay: DatasetReplayService):
        self._tasks, self._replay = tasks, replay

    def publish(
        self, access: WorkspaceAccess, task_id: UUID, output: str = "1m"
    ) -> PublishedDataset:
        access.require(Permission.DATA_MANAGE)
        task = self._tasks.get(access.workspace.workspace_id, task_id)
        request = task.request
        if request.workspace_id != access.workspace.workspace_id:
            raise PortError(ErrorCode.NOT_FOUND, "task unavailable")
        if task.status != "completed" or not task.collection_complete:
            raise PortError(ErrorCode.CONFLICT, "download must complete before publication")
        if request.source != "mairui.a-share.tick" or request.granularity != "tick":
            raise PortError(ErrorCode.UNSUPPORTED, "unsupported research source")
        if any(time.second or time.microsecond for time in (request.start, request.end)):
            raise PortError(ErrorCode.INVALID_INPUT, "research range must use complete minutes")
        if output not in {"1m", "tick"}:
            raise PortError(ErrorCode.INVALID_INPUT, "research output must be 1m or tick")
        start, end = request.start, request.end
        transformation = "mairui.trade-tick-to-1m.v1" if output == "1m" else "mairui.trade-tick.v1"
        return self._replay.replay(
            access,
            ReplayRequest(
                request.workspace_id,
                uuid5(task_id, f"research-{output}.v1"),
                task.batch_ids,
                transformation,
                start,
                end,
                request.created_at,
            ),
        )
