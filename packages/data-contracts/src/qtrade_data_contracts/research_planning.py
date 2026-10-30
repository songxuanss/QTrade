"""Mutable execution controls, kept outside frozen ResearchPlan semantics."""

from typing import Self

from pydantic import model_validator

from .collection import digest
from .models import Digest
from .planning import CollectionCostEstimate, CollectionPlanGuard
from .research_transport import ResearchPlan, ResearchPlanRequest


class ResearchPlanPreview(ResearchPlan):
    execution_estimate: CollectionCostEstimate
    plan_guard: CollectionPlanGuard
    confirmation_required: bool
    control_sha256: Digest

    def frozen_plan(self) -> ResearchPlan:
        """Persist only the original immutable contract, not execution controls."""
        return ResearchPlan.model_validate_json(
            self.model_dump_json(
                exclude={
                    "execution_estimate",
                    "plan_guard",
                    "confirmation_required",
                    "control_sha256",
                }
            )
        )

    @model_validator(mode="after")
    def valid_control(self) -> Self:
        # The inherited plan checksum retains its old inputs. A separate digest
        # binds operator policy and cost to this preview, never to old artifacts.
        if self.control_sha256 != digest(self.model_dump(mode="json", exclude={"control_sha256"})):
            raise ValueError("research preview control checksum mismatch")
        return self


class ResearchSubmission(ResearchPlanRequest):
    control_sha256: Digest | None = None
    confirmed: bool = False

    def selection(self) -> ResearchPlanRequest:
        """Strip mutable acknowledgements before freezing the original request."""
        return ResearchPlanRequest.model_validate_json(
            self.model_dump_json(exclude={"control_sha256", "confirmed"})
        )
