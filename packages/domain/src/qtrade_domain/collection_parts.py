"""Stable seven-day execution tasks, independent of overall download limits."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid5

from qtrade_domain.ingestion import CollectionRequest


def split_collection(request: CollectionRequest) -> tuple[CollectionRequest, ...]:
    if request.end - request.start <= timedelta(days=7):
        return (request,)
    ranges = []
    start = request.start
    while start < request.end:
        end = min(start + timedelta(days=7), request.end)
        ranges.append((start, end))
        start = end
    return tuple(
        replace(
            request,
            task_id=uuid5(
                request.task_id, f"collection-part.v1:{start.isoformat()}:{end.isoformat()}"
            ),
            start=start,
            end=end,
            download_group_id=request.task_id,
            part_index=index,
            part_count=len(ranges),
        )
        for index, (start, end) in enumerate(ranges, 1)
    )
