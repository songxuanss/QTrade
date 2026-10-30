"""Authorized schedule controls, independent of provider and database implementations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from qtrade_data_contracts.schedule_dispatch import ScheduleDispatchDecision
from qtrade_data_contracts.schedules import CollectionScheduleDraft, CollectionScheduleRecord
from qtrade_data_core.ports import DataAuthorizer, DataFailure
from qtrade_data_core.schedule_clock import next_slot


@dataclass(frozen=True)
class ScheduleLease:
    record: CollectionScheduleRecord
    token: UUID
    until: datetime


class ScheduleRepository(Protocol):
    def list(
        self, scope: UUID, *, limit: int = 100, offset: int = 0
    ) -> tuple[CollectionScheduleRecord, ...]: ...
    def get_dispatch(self, scope: UUID, run: UUID) -> ScheduleDispatchDecision: ...
    def freeze_dispatch(
        self, lease: ScheduleLease, decision: ScheduleDispatchDecision, now: datetime
    ) -> ScheduleDispatchDecision: ...
    def create(
        self, draft: CollectionScheduleDraft, next_due: datetime
    ) -> CollectionScheduleRecord: ...
    def reserve_run(self, lease: ScheduleLease, now: datetime) -> UUID: ...
    def pause(
        self,
        lease: ScheduleLease,
        reason: Literal["authorization", "source_unavailable", "approval_required"],
        now: datetime,
    ) -> None: ...
    def get(self, scope: UUID, schedule: UUID) -> CollectionScheduleRecord: ...
    def control(
        self, scope: UUID, schedule: UUID, revision: str, state: Literal["disabled", "enabled"]
    ) -> CollectionScheduleRecord: ...


class ScheduleControls:
    def __init__(self, auth: DataAuthorizer, repository: ScheduleRepository):
        self.auth, self.repository = auth, repository

    def create(self, draft: CollectionScheduleDraft, now: datetime) -> CollectionScheduleRecord:
        self.auth.require(draft.actor_id, draft.scope_id, "manage")
        return self.repository.create(draft, next_slot(draft, now))

    def list(
        self, actor: UUID, scope: UUID, *, limit: int = 100, offset: int = 0
    ) -> tuple[CollectionScheduleRecord, ...]:
        self.auth.require(actor, scope, "read")
        return self.repository.list(scope, limit=limit, offset=offset)

    def get(self, actor: UUID, scope: UUID, schedule: UUID) -> CollectionScheduleRecord:
        self.auth.require(actor, scope, "read")
        return self.repository.get(scope, schedule)

    def disable(
        self, actor: UUID, scope: UUID, schedule: UUID, revision: str
    ) -> CollectionScheduleRecord:
        self.auth.require(actor, scope, "manage")
        return self.repository.control(scope, schedule, revision, "disabled")

    def enable(
        self, actor: UUID, scope: UUID, schedule: UUID, revision: str
    ) -> CollectionScheduleRecord:
        self.auth.require(actor, scope, "manage")
        record = self.repository.get(scope, schedule)
        # An enabling administrator cannot replace the frozen execution actor.
        self.auth.require(record.draft.actor_id, scope, "manage")
        return self.repository.control(scope, schedule, revision, "enabled")

    def reserve_authorized_run(self, lease: ScheduleLease, now: datetime) -> UUID:
        draft = lease.record.draft
        # Dispatch uses the frozen execution actor's current permission, not
        # whichever administrator last enabled the plan. No cached grant or
        # another administrator can substitute for that actor's authorization.
        try:
            self.auth.require(draft.actor_id, draft.scope_id, "manage")
        except DataFailure as error:
            if error.code in {"forbidden", "unauthorized"}:
                self.repository.pause(lease, "authorization", now)
            # An unavailable permission service is not evidence of revocation.
            # Fail closed without reserving; recovery must recheck permission.
            raise
        return self.repository.reserve_run(lease, now)

    def freeze_dispatch(
        self, lease: ScheduleLease, decision: ScheduleDispatchDecision, now: datetime
    ) -> ScheduleDispatchDecision:
        # Recheck the execution actor even if the reservation predates an
        # outage. A journal entry is input evidence, never an execution grant.
        run = self.reserve_authorized_run(lease, now)
        if run != decision.run_id:
            raise DataFailure("conflict")
        return self.repository.freeze_dispatch(lease, decision, now)

    def get_dispatch(self, actor: UUID, scope: UUID, run: UUID) -> ScheduleDispatchDecision:
        self.auth.require(actor, scope, "read")
        return self.repository.get_dispatch(scope, run)
