"""Immutable slot decisions: explicit typed input, never a moving date window."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .collection import CollectionJobRequest, digest
from .models import DataContract, Text
from .research import StrictRecord
from .research_transport import ResearchPlanRequest
from .schedules import CollectionScheduleDraft, MarketScheduleSelection, ResearchScheduleSelection


class MarketDispatchInput(StrictRecord):
    kind: Literal["market"]
    request: CollectionJobRequest


class ResearchDispatchInput(StrictRecord):
    kind: Literal["research"]
    request: ResearchPlanRequest


class ScheduleDispatchDecision(DataContract):
    schedule_id: UUID
    run_id: UUID
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    due_at: AwareDatetime
    policy_version: Text
    basis: Text
    dispatch: Annotated[MarketDispatchInput | ResearchDispatchInput, Field(discriminator="kind")]
    frozen_at: AwareDatetime

    @model_validator(mode="after")
    def bound_request_identity(self) -> Self:
        request = self.dispatch.request
        if (
            request.scope_id != self.scope_id
            or request.request_id != self.run_id
            or request.correlation_id != self.run_id
            or request.mode != "missing"
        ):
            raise ValueError("dispatch request must belong to its reserved slot")
        return self

    def check_schedule(self, draft: CollectionScheduleDraft) -> None:
        """Bind frozen input to the entire draft revision, including pool/approval."""
        request, selection = self.dispatch.request, draft.selection
        if (
            self.scope_id != draft.scope_id
            or self.schedule_id != draft.schedule_id
            or self.revision != draft.revision
            or request.actor_id != draft.actor_id
            or request.source != selection.source
            or request.instrument_ids != selection.instrument_ids
        ):
            raise ValueError("dispatch does not match frozen schedule")
        if isinstance(self.dispatch, MarketDispatchInput) and isinstance(
            selection, MarketScheduleSelection
        ):
            if (
                self.dispatch.request.dataset_kind != selection.dataset_kind
                or self.dispatch.request.granularity != selection.granularity
                or self.dispatch.request.fields != selection.fields
            ):
                raise ValueError("market dispatch selection changed")
        elif isinstance(self.dispatch, ResearchDispatchInput) and isinstance(
            selection, ResearchScheduleSelection
        ):
            if (
                self.dispatch.request.kind != selection.dataset_kind
                or self.dispatch.request.date_axis != selection.date_axis
            ):
                raise ValueError("research dispatch selection changed")
        else:
            raise ValueError("dispatch category changed")

    @property
    def content_sha256(self) -> str:
        return digest(self.model_dump(mode="json"))
