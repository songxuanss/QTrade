"""Private response capture is separate from parsed RAW pages and publications."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID, uuid4

from qtrade_data_contracts.research_acquisition import ResearchResponsePage, ResearchResponseReceipt
from qtrade_data_contracts.research_transport import (
    ResearchPartition,
    ResearchPlan,
    ResearchPlanRequest,
)

from .ports import DataFailure
from .research import ResearchRawPage


@dataclass(frozen=True)
class ResearchSourceResponse:
    """Private decoded HTTP bytes; never serialize this object into public APIs."""

    operation: Literal["research.data"]
    status: int | None
    body: bytes = field(repr=False)
    complete: bool


ResponseSink = Callable[[ResearchSourceResponse], None]


@runtime_checkable
class CapturedResearchProvider(Protocol):
    """Call the sink synchronously before interpretation; never retain it."""

    def fetch_with_capture(
        self,
        request: ResearchPlanRequest,
        partition: ResearchPartition,
        cursor: str | None,
        capture: ResponseSink,
    ) -> ResearchRawPage: ...


class ResearchResponseArchive(Protocol):
    def capture_response(
        self,
        plan: ResearchPlan,
        partition: ResearchPartition,
        cursor: str | None,
        acquisition: UUID,
        index: int,
        response: ResearchSourceResponse,
        at: datetime,
    ) -> ResearchResponseReceipt: ...


class ResearchResponseStore(Protocol):
    def append(self, plan: ResearchPlan, receipt: ResearchResponseReceipt) -> None: ...
    def page(
        self,
        plan: ResearchPlan,
        limit: int,
        after: UUID | None,
    ) -> ResearchResponsePage: ...


def matches_plan(receipt: ResearchResponseReceipt, plan: ResearchPlan) -> bool:
    return (
        receipt.scope_id,
        receipt.request_id,
        receipt.actor_id,
        receipt.plan_sha256,
        receipt.source,
        receipt.kind,
        receipt.mapping_version,
    ) == (
        plan.request.scope_id,
        plan.request.request_id,
        plan.request.actor_id,
        plan.plan_sha256,
        plan.capability.source,
        plan.capability.kind,
        plan.capability.mapping_version,
    ) and any(
        (p.instrument_id, p.start, p.end) == (receipt.instrument_id, receipt.start, receipt.end)
        for p in plan.partitions
    )


class ResearchAcquisitionEvidence:
    """Archive/fsync first, then append immutable metadata; fail before decode.

    Receipt creation is internal to an already-authorized fetch. It does not
    imply a valid parsed page, source availability, PIT or a successful download.
    """

    def __init__(self, archive: ResearchResponseArchive, store: ResearchResponseStore):
        self.archive, self.store = archive, store

    def fetch(
        self,
        plan: ResearchPlan,
        provider: CapturedResearchProvider,
        partition: ResearchPartition,
        cursor: str | None,
        clock: Callable[[], datetime],
    ) -> ResearchRawPage:
        """Own a single synchronous capture window; reject retained callbacks.

        The window belongs to this exact fetch, not mutable outer pagination
        variables. Storage failure prevents the provider returning a usable page.
        """
        acquisition, count, accepting, poisoned = uuid4(), 0, True, False

        def capture(response: ResearchSourceResponse) -> None:
            nonlocal count, poisoned
            if not accepting:
                raise DataFailure("conflict")
            if count >= 10:
                poisoned = True
                raise DataFailure("unverified")
            try:
                self.record(plan, partition, cursor, acquisition, count, response, clock())
            except Exception:
                poisoned = True
                raise
            count += 1

        try:
            page = provider.fetch_with_capture(plan.request, partition, cursor, capture)
            if poisoned:
                raise DataFailure("unavailable")
            if count == 0:
                raise DataFailure("unverified")
            return page
        finally:
            accepting = False

    def record(
        self,
        plan: ResearchPlan,
        partition: ResearchPartition,
        cursor: str | None,
        acquisition: UUID,
        index: int,
        response: ResearchSourceResponse,
        at: datetime,
    ) -> ResearchResponseReceipt:
        receipt = self.archive.capture_response(
            plan, partition, cursor, acquisition, index, response, at
        )
        if not matches_plan(receipt, plan):
            raise DataFailure("unverified")
        self.store.append(plan, receipt)
        return receipt
