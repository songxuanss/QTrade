"""Public offline intent metadata. RAW bodies and lease tokens never cross this seam."""

from typing import Annotated, Literal, Self
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .collection import (
    CollectionJobRequest,
    CollectionPeriod,
    FactorDefinition,
    RawPayloadKind,
    digest,
)
from .models import Digest, Text
from .normalization_input import InputValue


class ReplayV2(InputValue):
    schema_version: Literal[2] = 2

    @field_validator("schema_version", mode="before")
    @classmethod
    def version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("unsupported replay version")
        return value


class CollectionReplayQueueRequest(ReplayV2):
    """Internal queue envelope: pre-replay v1 Workers must reject, never fetch."""

    execution_kind: Literal["normalization_replay"] = "normalization_replay"
    source: Text
    request: CollectionJobRequest

    @model_validator(mode="after")
    def same_source(self) -> Self:
        if self.source != self.request.source:
            raise ValueError("queue source disagrees with frozen request")
        return self


class CollectionReplayLimits(ReplayV2):
    max_rows: Annotated[int, Field(ge=1, le=100000)] = 100000
    max_bytes: Annotated[int, Field(ge=1, le=128 * 1024 * 1024)] = 128 * 1024 * 1024
    max_receipts: Annotated[int, Field(ge=1, le=4096)] = 4096


class CollectionReplayRequest(ReplayV2):
    actor_id: UUID
    scope_id: UUID
    request_id: UUID
    parent_job_id: UUID
    parent_part_id: UUID
    input_id: UUID

    @model_validator(mode="after")
    def new_identity(self) -> Self:
        if self.request_id == self.parent_job_id:
            raise ValueError("replay requires a new request")
        return self


class CollectionReplayParent(ReplayV2):
    request: CollectionJobRequest
    job_id: UUID
    part_id: UUID
    instrument_id: Text
    field: Text | None
    start: AwareDatetime
    end: AwareDatetime
    mapping_version: Text
    instrument_name: Text
    execution_kind: Literal["collection", "normalization_replay"]

    @model_validator(mode="after")
    def valid_parent(self) -> Self:
        if (
            self.job_id != self.request.request_id
            or self.instrument_id not in self.request.instrument_ids
            or not self.request.start <= self.start < self.end <= self.request.end
        ):
            raise ValueError("invalid parent part selection")
        return self


class CollectionInputReference(ReplayV2):
    scope_id: UUID
    owner_job_id: UUID
    part_id: UUID
    input_id: UUID
    input_sha256: Digest
    instrument_id: Text
    instrument_name: Text
    field: Text | None
    source: Text
    dataset_kind: Literal["bars", "factors"]
    granularity: CollectionPeriod
    start: AwareDatetime
    end: AwareDatetime
    mapping_version: Text
    prepared_at: AwareDatetime
    row_count: Annotated[int, Field(ge=0, le=100000)]
    factor_definition: FactorDefinition | None


class CollectionReplaySource(ReplayV2):
    capture_id: UUID
    kind: RawPayloadKind
    content_sha256: Digest
    captured_at: AwareDatetime
    response_rows: Annotated[int, Field(ge=0)] | None


class CollectionReplay(ReplayV2):
    replay_version: Literal["collection-normalization-replay.v1"] = (
        "collection-normalization-replay.v1"
    )
    command: CollectionReplayRequest
    parent: CollectionReplayParent
    input: CollectionInputReference
    sources: Annotated[tuple[CollectionReplaySource, ...], Field(min_length=1, max_length=4096)]
    target_request: CollectionJobRequest
    target_part_id: UUID
    target_mapping_version: Text
    rule_version: Text
    timezone: Text
    verified_bytes: Annotated[int, Field(ge=1)]
    limits: CollectionReplayLimits
    supplier_calls: Literal[0] = 0
    control_sha256: Digest

    @model_validator(mode="after")
    def frozen_selection(self) -> Self:
        cmd, parent, source, target = self.command, self.parent, self.input, self.target_request
        if (
            cmd.scope_id != parent.request.scope_id
            or cmd.scope_id != source.scope_id
            or cmd.parent_part_id != parent.part_id
            or cmd.input_id != source.input_id
            or (cmd.actor_id, cmd.scope_id, cmd.request_id)
            != (target.actor_id, target.scope_id, target.request_id)
            or cmd.request_id in {parent.job_id, source.owner_job_id}
            or self.target_part_id != uuid5(cmd.request_id, "normalization-replay")
            or target.mode != "all"
            or target.instrument_ids != (source.instrument_id,)
            or target.fields != ((source.field,) if source.field else ())
            or (target.source, target.dataset_kind, target.granularity, target.start, target.end)
            != (source.source, source.dataset_kind, source.granularity, source.start, source.end)
            or (
                parent.instrument_id,
                parent.field,
                parent.start,
                parent.end,
                parent.request.source,
                parent.request.dataset_kind,
                parent.request.granularity,
            )
            != (
                source.instrument_id,
                source.field,
                source.start,
                source.end,
                source.source,
                source.dataset_kind,
                source.granularity,
            )
        ):
            raise ValueError("replay cannot expand or switch its parent part")
        if (
            source.row_count > self.limits.max_rows
            or self.verified_bytes > self.limits.max_bytes
            or len(self.sources) > self.limits.max_receipts
            or len({s.capture_id for s in self.sources}) != len(self.sources)
            or any(s.captured_at > source.prepared_at for s in self.sources)
            or (source.dataset_kind == "factors") != (source.factor_definition is not None)
        ):
            raise ValueError("invalid replay input limits/evidence")
        if self.control_sha256 != digest(self.model_dump(mode="json", exclude={"control_sha256"})):
            raise ValueError("replay checksum mismatch")
        return self


class CollectionReplaySubmission(CollectionReplayRequest):
    control_sha256: Digest

    def command(self) -> CollectionReplayRequest:
        return CollectionReplayRequest.model_validate_json(
            self.model_dump_json(exclude={"control_sha256"})
        )


class CollectionReplayInputs(ReplayV2):
    scope_id: UUID
    job_id: UUID
    part_id: UUID
    parent: CollectionReplayParent
    inputs: Annotated[tuple[CollectionInputReference, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def exact_catalog(self) -> Self:
        parent = self.parent
        if (parent.request.scope_id, parent.part_id) != (self.scope_id, self.part_id):
            raise ValueError("input catalog identity mismatch")
        if len({v.input_id for v in self.inputs}) != len(self.inputs) or any(
            (
                v.scope_id,
                v.instrument_id,
                v.field,
                v.source,
                v.dataset_kind,
                v.granularity,
                v.start,
                v.end,
            )
            != (
                self.scope_id,
                parent.instrument_id,
                parent.field,
                parent.request.source,
                parent.request.dataset_kind,
                parent.request.granularity,
                parent.start,
                parent.end,
            )
            for v in self.inputs
        ):
            raise ValueError("input catalog selection mismatch")
        return self
