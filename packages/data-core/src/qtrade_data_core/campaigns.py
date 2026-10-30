"""Frozen stock-pool plans and resumable bounded expansion, without business imports."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from qtrade_data_contracts import (
    CampaignPlan,
    CollectionRange,
    CollectionSelection,
    MemberCollectionPlan,
    ReferenceIdentity,
)
from qtrade_data_core.ports import DataAuthorizer, DataFailure, UniverseRepository


@dataclass(frozen=True)
class Coverage:
    existing: tuple[CollectionRange, ...]
    missing: tuple[CollectionRange, ...]
    reuse: tuple[UUID, ...]
    completed: tuple[UUID, ...]
    fingerprint: str


@dataclass(frozen=True)
class Work:
    ordinal: int
    instrument: ReferenceIdentity
    start: datetime
    end: datetime
    task_id: UUID
    reuse: bool


class CollectionExecutor(Protocol):
    def coverage(
        self, selection: CollectionSelection, instrument: ReferenceIdentity
    ) -> Coverage: ...
    def submit(self, selection: CollectionSelection, work: Work, created_at: datetime) -> None: ...
    def result(self, selection: CollectionSelection, task_id: UUID) -> dict[str, object]: ...
    def retry(self, selection: CollectionSelection, task_id: UUID) -> None: ...


class CampaignRepository(Protocol):
    def save_plan(self, plan: CampaignPlan) -> None: ...
    def plan(self, scope_id: UUID, plan_id: UUID) -> CampaignPlan: ...
    def submit(self, plan: CampaignPlan) -> UUID: ...
    def find(self, scope_id: UUID, request_id: UUID) -> tuple[UUID, UUID] | None: ...
    def run(
        self, scope_id: UUID, campaign_id: UUID, member_offset: int = 0
    ) -> dict[str, object]: ...
    def runs(self, scope_id: UUID, limit: int) -> list[dict[str, object]]: ...
    def ready(self, limit: int) -> tuple[tuple[UUID, UUID], ...]: ...
    def expanded(self, scope_id: UUID, campaign_id: UUID) -> int: ...
    def record(self, scope_id: UUID, campaign_id: UUID, work: Work) -> None: ...
    def active(
        self, scope_id: UUID, campaign_id: UUID, limit: int
    ) -> tuple[tuple[int, UUID], ...]: ...
    def result(
        self, scope_id: UUID, campaign_id: UUID, ordinal: int, result: dict[str, object]
    ) -> None: ...
    def finish(self, scope_id: UUID, campaign_id: UUID) -> None: ...
    def control(self, scope_id: UUID, campaign_id: UUID, action: str) -> tuple[UUID, ...]: ...


def work_at(plan: CampaignPlan, campaign_id: UUID, ordinal: int) -> Work:
    """Calculate one seven-day piece without allocating an unbounded date list."""
    remaining = ordinal
    for member in plan.members:
        if remaining >= member.planned_parts:
            remaining -= member.planned_parts
            continue
        if remaining < len(member.reused_task_ids):
            return Work(
                ordinal,
                member.instrument,
                plan.selection.start,
                plan.selection.end,
                member.reused_task_ids[remaining],
                True,
            )
        remaining -= len(member.reused_task_ids)
        for span in member.submit_ranges:
            duration = span.end - span.start
            count = duration // timedelta(days=7) + bool(duration % timedelta(days=7))
            if remaining >= count:
                remaining -= count
                continue
            start = span.start + remaining * timedelta(days=7)
            end = min(start + timedelta(days=7), span.end)
            identity = uuid5(
                campaign_id,
                f"part.v1:{member.instrument.instrument_id}:{start.isoformat()}:{end.isoformat()}",
            )
            return Work(ordinal, member.instrument, start, end, identity, False)
    raise DataFailure("invalid_input")


class CollectionGateway:
    def __init__(
        self,
        auth: DataAuthorizer,
        universes: UniverseRepository,
        repository: CampaignRepository,
        executor: CollectionExecutor,
    ):
        self._auth, self._universes, self._repository, self._executor = (
            auth,
            universes,
            repository,
            executor,
        )

    def _build(self, selection: CollectionSelection, now: datetime, plan_id: UUID) -> CampaignPlan:
        self._auth.require(selection.actor_id, selection.scope_id, "manage")
        members: dict[str, tuple[ReferenceIdentity, set[str]]] = {}
        for snapshot_id in selection.snapshot_ids:
            snapshot = self._universes.get(selection.scope_id, snapshot_id)
            if snapshot.scope_id != selection.scope_id or snapshot.snapshot_id != snapshot_id:
                raise DataFailure("unavailable")
            for member in snapshot.members:
                identity = member.instrument.instrument_id
                if identity not in members:
                    members[identity] = member.instrument, set()
                members[identity][1].update((*member.affiliations, snapshot.universe_id))
        selected = set(selection.instrument_ids)
        if selected - members.keys():
            raise DataFailure("invalid_input")
        plans = []
        versions = []
        for identity in sorted(members):
            if selected and identity not in selected:
                continue
            instrument, affiliations = members[identity]
            coverage = self._executor.coverage(selection, instrument)
            spans = (
                (CollectionRange(start=selection.start, end=selection.end),)
                if selection.mode == "all"
                else coverage.missing
            )
            reuse = () if selection.mode == "all" else coverage.reuse + coverage.completed
            count = sum(
                (span.end - span.start) // timedelta(days=7)
                + bool((span.end - span.start) % timedelta(days=7))
                for span in spans
            ) + len(reuse)
            plans.append(
                MemberCollectionPlan(
                    instrument=instrument,
                    affiliations=tuple(sorted(affiliations)),
                    existing_ranges=coverage.existing,
                    submit_ranges=spans,
                    planned_parts=count,
                    reused_task_ids=reuse,
                    completed_task_ids=(),
                )
            )
            versions.append((identity, coverage.fingerprint))
        self._auth.require(selection.actor_id, selection.scope_id, "manage")
        version = sha256(json.dumps(versions, separators=(",", ":")).encode()).hexdigest()
        return CampaignPlan(
            schema_version=1,
            scope_id=selection.scope_id,
            plan_id=plan_id,
            selection=selection,
            coverage_version=version,
            created_at=now,
            expires_at=now + timedelta(minutes=30),
            members=tuple(plans),
            planned_parts=sum(p.planned_parts for p in plans),
        )

    def preview(self, selection: CollectionSelection) -> CampaignPlan:
        plan = self._build(selection, datetime.now(UTC), uuid4())
        self._repository.save_plan(plan)
        return plan

    def submit(self, actor_id: UUID, scope_id: UUID, plan_id: UUID, version: str) -> UUID:
        self._auth.require(actor_id, scope_id, "manage")
        plan = self._repository.plan(scope_id, plan_id)
        if plan.selection.actor_id != actor_id or plan.coverage_version != version:
            raise DataFailure("conflict")
        existing = self._repository.find(scope_id, plan.selection.request_id)
        if existing:
            campaign_id, existing_plan = existing
            if existing_plan != plan_id:
                raise DataFailure("conflict")
            return campaign_id
        if plan.expires_at <= datetime.now(UTC):
            raise DataFailure("conflict")
        current = self._build(plan.selection, datetime.now(UTC), plan_id)
        if current.coverage_version != version:
            raise DataFailure("conflict")
        self._auth.require(actor_id, scope_id, "manage")
        return self._repository.submit(plan)

    def run(
        self, actor_id: UUID, scope_id: UUID, campaign_id: UUID, member_offset: int = 0
    ) -> dict[str, object]:
        self._auth.require(actor_id, scope_id, "read")
        return self._repository.run(scope_id, campaign_id, member_offset)

    def runs(self, actor_id: UUID, scope_id: UUID) -> list[dict[str, object]]:
        self._auth.require(actor_id, scope_id, "read")
        return self._repository.runs(scope_id, 20)

    def process(self, scope_id: UUID, campaign_id: UUID, page_size: int = 25) -> None:
        run = self._repository.run(scope_id, campaign_id)
        plan = self._repository.plan(scope_id, UUID(str(run["plan_id"])))
        self._auth.require(plan.selection.actor_id, scope_id, "manage")
        if run["state"] != "cancelled":
            expanded = self._repository.expanded(scope_id, campaign_id)
            for ordinal in range(expanded, min(expanded + page_size, plan.planned_parts)):
                # Fresh authorization immediately before each side effect.
                self._auth.require(plan.selection.actor_id, scope_id, "manage")
                if self._repository.run(scope_id, campaign_id)["state"] == "cancelled":
                    break
                work = work_at(plan, campaign_id, ordinal)
                try:
                    self._executor.submit(plan.selection, work, plan.created_at)
                except DataFailure as error:
                    if error.code in ("forbidden", "unauthorized"):
                        raise
                    self._repository.record(scope_id, campaign_id, work)
                    self._repository.result(
                        scope_id,
                        campaign_id,
                        ordinal,
                        {"state": "failed", "error_code": error.code},
                    )
                    continue
                self._repository.record(scope_id, campaign_id, work)
        for ordinal, task_id in self._repository.active(scope_id, campaign_id, 100):
            result = self._executor.result(plan.selection, task_id)
            self._repository.result(scope_id, campaign_id, ordinal, result)
        self._repository.finish(scope_id, campaign_id)

    def ready(self) -> Iterator[tuple[UUID, UUID]]:
        yield from self._repository.ready(20)

    def control(self, actor_id: UUID, scope_id: UUID, campaign_id: UUID, action: str) -> None:
        self._auth.require(actor_id, scope_id, "manage")
        run = self._repository.run(scope_id, campaign_id)
        plan = self._repository.plan(scope_id, UUID(str(run["plan_id"])))
        if action == "retry" and not run["failed_parts"]:
            return
        if action == "cancel" and run["state"] not in ("queued", "running"):
            raise DataFailure("conflict")
        if action == "retry":
            for task_id in self._repository.control(scope_id, campaign_id, "failed"):
                self._executor.retry(plan.selection, task_id)
        self._repository.control(scope_id, campaign_id, action)
