"""Authorized research submission/status; preview remains without side effects."""

from typing import Protocol
from uuid import UUID

from qtrade_data_contracts.research_acquisition import ResearchResponsePage
from qtrade_data_contracts.research_processing import ResearchProcessingFailure
from qtrade_data_contracts.research_transport import ResearchPlan, ResearchPlanRequest
from qtrade_data_core.ports import DataAuthorizer, DataFailure
from qtrade_data_core.research import ResearchService
from qtrade_data_core.research_acquisition import ResearchResponseStore, matches_plan


class ResearchJobRepository(Protocol):
    def plan(self, scope: UUID, request: UUID) -> ResearchPlan: ...
    def enqueue(self, plan: ResearchPlan) -> None: ...
    def jobs(self, scope: UUID, *, limit: int = 100) -> tuple[dict[str, object], ...]: ...
    def job(self, scope: UUID, request: UUID) -> dict[str, object]: ...
    def retry(self, scope: UUID, request: UUID) -> None: ...


class ResearchJobsService:
    def __init__(
        self,
        auth: DataAuthorizer,
        research: ResearchService,
        repository: ResearchJobRepository,
        *,
        responses: ResearchResponseStore | None = None,
    ):
        self.auth, self.research, self.repository = auth, research, repository
        self.responses = responses

    def response_receipts(
        self,
        actor: UUID,
        scope: UUID,
        request: UUID,
        limit: int = 20,
        after: UUID | None = None,
    ) -> ResearchResponsePage:
        """Read historical transport facts, without supplier I/O or private bodies."""
        self.auth.require(actor, scope, "read")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DataFailure("invalid_input")
        self.repository.job(scope, request)
        plan = self.repository.plan(scope, request)
        if (plan.request.scope_id, plan.request.request_id) != (scope, request):
            raise DataFailure("unverified")
        if self.responses is None:
            raise DataFailure("unavailable")
        value = self.responses.page(plan, limit, after)
        try:
            page = ResearchResponsePage.model_validate_json(value.model_dump_json())
        except ValueError:
            raise DataFailure("unverified") from None
        if (
            (page.scope_id, page.request_id) != (scope, request)
            or len(page.receipts) > limit
            or (page.next_id is not None and len(page.receipts) != limit)
            or any(not matches_plan(r, plan) or r.receipt_id == after for r in page.receipts)
        ):
            raise DataFailure("unverified")
        return page

    def submit(
        self,
        request: ResearchPlanRequest,
        control_sha256: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, object]:
        self.auth.require(request.actor_id, request.scope_id, "manage")
        try:
            existing = self.repository.job(request.scope_id, request.request_id)
        except DataFailure as error:
            if error.code != "not_found":
                raise
        else:
            # Repeated acceptance keeps the original frozen request, even if
            # operator thresholds changed. Authorization is still current.
            if self.repository.plan(request.scope_id, request.request_id).request != request:
                raise DataFailure("conflict")
            return existing
        preview = self.research.controlled_preview(request)
        if control_sha256 is not None and preview.control_sha256 != control_sha256:
            raise DataFailure("conflict")
        if preview.confirmation_required and (confirmed is not True or control_sha256 is None):
            raise DataFailure("invalid_input")
        # Do not persist preview/acknowledgement fields in the immutable plan.
        self.repository.enqueue(preview.frozen_plan())
        return self.repository.job(request.scope_id, request.request_id)

    def job(self, actor: UUID, scope: UUID, request: UUID) -> dict[str, object]:
        self.auth.require(actor, scope, "read")
        return self.repository.job(scope, request)

    def processing_failures(
        self, actor: UUID, scope: UUID, request: UUID
    ) -> tuple[ResearchProcessingFailure, ...]:
        self.auth.require(actor, scope, "read")
        self.repository.job(scope, request)
        return self.research.processing_failures(actor, scope, request)

    def jobs(self, actor: UUID, scope: UUID) -> tuple[dict[str, object], ...]:
        self.auth.require(actor, scope, "read")
        return self.repository.jobs(scope)

    def retry(self, actor: UUID, scope: UUID, request: UUID) -> dict[str, object]:
        # Control permission is current; the frozen execution actor is retained
        # and must still pass authorization when the Worker resumes the run.
        self.auth.require(actor, scope, "manage")
        self.repository.retry(scope, request)
        return self.repository.job(scope, request)
