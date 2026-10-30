"""One bounded scheduling quantum; submission is not download completion."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from qtrade_data_contracts.models import ErrorCode
from qtrade_data_contracts.schedule_dispatch import MarketDispatchInput, ScheduleDispatchDecision
from qtrade_data_contracts.schedules import CollectionScheduleDraft
from qtrade_data_core.collection import CollectionService
from qtrade_data_core.ports import DataAuthorizer, DataFailure
from qtrade_data_core.schedule_approval import ScheduleApprovalRequired, ScheduleExecutionApproval
from qtrade_data_core.schedule_clock import due_slots, next_slot
from qtrade_data_core.schedules import ScheduleControls, ScheduleLease, ScheduleRepository
from qtrade_data_core.usage import UsageDenied
from qtrade_data_core.usage_execution import usage_scope


class ScheduleRuntimeRepository(ScheduleRepository, Protocol):
    def claim(self, now: datetime, *, lease_seconds: int = 60) -> ScheduleLease | None: ...
    def advance(self, lease: ScheduleLease, next_due: datetime, now: datetime) -> None: ...


class SchedulePolicy(Protocol):
    def prepare(
        self, draft: CollectionScheduleDraft, run: UUID, due: datetime, now: datetime
    ) -> ScheduleDispatchDecision | None: ...


@dataclass(frozen=True)
class ScheduleTick:
    scope_id: UUID
    schedule_id: UUID
    state: Literal["submitted", "calendar_skipped", "window_skipped", "paused", "deferred"]
    run_id: UUID | None = None
    error_code: ErrorCode | None = None


class ScheduleRunner:
    def __init__(
        self,
        auth: DataAuthorizer,
        repository: ScheduleRuntimeRepository,
        policy: SchedulePolicy,
        submitter: Callable[[ScheduleLease, UUID], CollectionService],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.auth, self.repository, self.policy = auth, repository, policy
        self.submitter, self.clock = submitter, clock
        self.controls = ScheduleControls(auth, repository)

    def _pause(
        self,
        lease: ScheduleLease,
        reason: Literal["authorization", "source_unavailable", "approval_required"],
        code: ErrorCode,
    ) -> ScheduleTick:
        draft = lease.record.draft
        try:
            self.repository.pause(lease, reason, self.clock())
        except DataFailure as error:
            if error.code != "conflict":
                raise
            return ScheduleTick(
                draft.scope_id, draft.schedule_id, "deferred", error_code="conflict"
            )
        return ScheduleTick(draft.scope_id, draft.schedule_id, "paused", error_code=code)

    def run_once(self) -> ScheduleTick | None:
        now = self.clock()
        lease = self.repository.claim(now, lease_seconds=600)
        if lease is None:
            return None
        draft, due = lease.record.draft, lease.record.next_due_at
        try:
            self.auth.require(draft.actor_id, draft.scope_id, "manage")
        except DataFailure as error:
            if error.code in {"forbidden", "unauthorized"}:
                return self._pause(lease, "authorization", error.code)
            return ScheduleTick(
                draft.scope_id, draft.schedule_id, "deferred", error_code=error.code
            )
        try:
            recovery = due_slots(draft, due, now)
            if not recovery.slots or recovery.slots[0] != due:
                target = recovery.slots[0] if recovery.slots else recovery.next_due
                self.repository.advance(lease, target, self.clock())
                return ScheduleTick(draft.scope_id, draft.schedule_id, "window_skipped")
            run = self.controls.reserve_authorized_run(lease, self.clock())
            try:
                decision = self.repository.get_dispatch(draft.scope_id, run)
            except DataFailure as error:
                if error.code != "not_found":
                    raise
                with usage_scope(draft.actor_id, draft.scope_id):
                    prepared = self.policy.prepare(draft, run, due, self.clock())
                if prepared is None:
                    self.repository.advance(lease, next_slot(draft, due), self.clock())
                    return ScheduleTick(draft.scope_id, draft.schedule_id, "calendar_skipped", run)
                decision = self.controls.freeze_dispatch(lease, prepared, self.clock())
            decision.check_schedule(draft)
            if decision.due_at != due or not isinstance(decision.dispatch, MarketDispatchInput):
                raise DataFailure("unverified")
            service = self.submitter(lease, run)
            request = decision.dispatch.request
            preview = service.preview(request)
            service.submit(
                request,
                preview.coverage_version,
                automatic_approval=ScheduleExecutionApproval(draft),
            )
            # If this update loses ownership, the accepted request remains
            # immutable. Recovery finds its frozen identity and submits idempotently.
            self.repository.advance(lease, next_slot(draft, due), self.clock())
            return ScheduleTick(draft.scope_id, draft.schedule_id, "submitted", run)
        except ScheduleApprovalRequired:
            return self._pause(lease, "approval_required", "invalid_input")
        except UsageDenied:
            # Metadata budget denials do not prove source failure. No HTTP was
            # admitted; retry only after the persistent lease can be reclaimed.
            return ScheduleTick(
                draft.scope_id, draft.schedule_id, "deferred", error_code="rate_limited"
            )
        except DataFailure as error:
            if error.code in {"conflict", "rate_limited"}:
                return ScheduleTick(
                    draft.scope_id, draft.schedule_id, "deferred", error_code=error.code
                )
            if error.code == "forbidden":
                return self._pause(lease, "authorization", error.code)
            if error.code == "invalid_input":
                return self._pause(lease, "approval_required", error.code)
            return self._pause(lease, "source_unavailable", error.code)
        except ValueError:
            return self._pause(lease, "source_unavailable", "unverified")
