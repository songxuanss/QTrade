"""Frozen offline inputs, separate from supplier collection and old publications."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .collection import digest
from .models import Digest
from .research import ResearchV2
from .research_archive import ResearchRawManifest
from .research_transport import ResearchPlan


class ResearchReplayLimits(ResearchV2):
    max_pages: Annotated[int, Field(ge=1, le=1000)] = 1000
    max_rows: Annotated[int, Field(ge=1, le=100000)] = 100000
    max_bytes: Annotated[int, Field(ge=1, le=64 * 1024 * 1024)] = 64 * 1024 * 1024


class ResearchReplayRequest(ResearchV2):
    actor_id: UUID
    scope_id: UUID
    request_id: UUID
    parent_request_id: UUID

    @model_validator(mode="after")
    def distinct_identity(self) -> Self:
        if self.request_id == self.parent_request_id:
            raise ValueError("replay must have a new execution identity")
        return self


class ResearchReplay(ResearchV2):
    replay_version: Literal["research-raw-replay.v1"] = "research-raw-replay.v1"
    command: ResearchReplayRequest
    parent_plan: ResearchPlan
    target_plan: ResearchPlan
    source_batches: Annotated[tuple[ResearchRawManifest, ...], Field(min_length=1, max_length=1000)]
    limits: ResearchReplayLimits
    control_sha256: Digest

    @model_validator(mode="after")
    def frozen_lineage(self) -> Self:
        command, parent, target = self.command, self.parent_plan.request, self.target_plan.request
        if (command.scope_id, command.parent_request_id) != (parent.scope_id, parent.request_id):
            raise ValueError("replay parent identity mismatch")
        if (command.actor_id, command.scope_id, command.request_id) != (
            target.actor_id,
            target.scope_id,
            target.request_id,
        ):
            raise ValueError("replay target identity mismatch")
        axes = ("source", "kind", "instrument_ids", "date_axis", "start", "end")
        if any(getattr(parent, key) != getattr(target, key) for key in axes):
            raise ValueError("offline replay cannot expand or switch the parent selection")
        if self.parent_plan.partitions != self.target_plan.partitions:
            raise ValueError("replay partitions must remain frozen")
        if (
            self.target_plan.plan_version != "research-replay-plan.v1"
            or self.target_plan.estimated_calls_min != 0
            or self.target_plan.estimated_calls_max != 0
            or len(self.source_batches) > self.limits.max_pages
            or sum(b.raw_record_count for b in self.source_batches) > self.limits.max_rows
            or len({b.batch_id for b in self.source_batches}) != len(self.source_batches)
        ):
            raise ValueError("invalid offline input bounds or plan")
        if any(
            (b.scope_id, b.request_id, b.plan_sha256, b.source, b.kind)
            != (
                parent.scope_id,
                parent.request_id,
                self.parent_plan.plan_sha256,
                parent.source,
                parent.kind,
            )
            for b in self.source_batches
        ):
            raise ValueError("replay raw input identity mismatch")
        if self.control_sha256 != digest(self.model_dump(mode="json", exclude={"control_sha256"})):
            raise ValueError("replay checksum mismatch")
        return self


class ResearchReplaySubmission(ResearchReplayRequest):
    control_sha256: Digest

    def command(self) -> ResearchReplayRequest:
        return ResearchReplayRequest.model_validate_json(
            self.model_dump_json(exclude={"control_sha256"})
        )
