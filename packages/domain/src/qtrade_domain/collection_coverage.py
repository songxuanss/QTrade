"""Half-open collection coverage and conservative checkpoint reuse planning."""

import re
from dataclasses import dataclass
from datetime import datetime

from qtrade_domain.ingestion import CollectionRequest, IngestionTask

Interval = tuple[datetime, datetime]


def instrument_key(source: str, instrument: str) -> str:
    if source in {"mairui.a-share.tick", "mairui.a-share.bar"}:
        match = re.fullmatch(r"(?:CN\.)?(\d{6})(?:\.(?:SH|SZ|BJ))?", instrument.upper())
        if match:
            return match[1]
    return instrument


def matches(request: CollectionRequest, task: IngestionTask) -> bool:
    other = task.request
    return (
        other.workspace_id == request.workspace_id
        and other.source == request.source
        and other.authorization_id == request.authorization_id
        and other.granularity == request.granularity
        and instrument_key(other.source, other.instrument_id)
        == instrument_key(request.source, request.instrument_id)
        and other.start < request.end
        and other.end > request.start
    )


def subtract(start: datetime, end: datetime, covered: tuple[Interval, ...]) -> tuple[Interval, ...]:
    cursor = start
    gaps = []
    for left, right in sorted(covered):
        left, right = max(start, left), min(end, right)
        if right <= cursor:
            continue
        if left > cursor:
            gaps.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < end:
        gaps.append((cursor, end))
    return tuple(gaps)


@dataclass(frozen=True)
class CollectionPlan:
    status: str
    missing: tuple[Interval, ...]
    submit_ranges: tuple[Interval, ...]
    reuse: tuple[IngestionTask, ...]
    overlapping: tuple[IngestionTask, ...]


def plan_collection(request: CollectionRequest, tasks: tuple[IngestionTask, ...]) -> CollectionPlan:
    overlapping = tuple(task for task in tasks if matches(request, task))
    completed = tuple(
        (task.request.start, task.request.end) for task in overlapping if task.status == "completed"
    )
    missing = subtract(request.start, request.end, completed)
    # Only resume a failed task fully inside the requested window. Its fenced
    # checkpoint preserves pages; individual tick timestamps do not prove coverage.
    reuse = (
        tuple(
            task
            for task in overlapping
            if task.status in {"queued", "running", "waiting"}
            or (
                task.status == "failed"
                and task.request.start >= request.start
                and task.request.end <= request.end
                and not any(
                    left < task.request.end and right > task.request.start
                    for left, right in completed
                )
            )
        )
        if missing
        else ()
    )
    reserved = completed + tuple((task.request.start, task.request.end) for task in reuse)
    status = "complete" if not missing else "partial" if overlapping else "new"
    return CollectionPlan(
        status, missing, subtract(request.start, request.end, reserved), reuse, overlapping
    )
