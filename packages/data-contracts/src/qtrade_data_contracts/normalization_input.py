"""Additive v2 conversion checkpoints; existing v1 manifests remain unchanged."""

from datetime import date
from math import isfinite
from typing import Annotated, Literal, Self
from uuid import UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .collection import CollectionJobRequest, FactorDefinition, digest
from .models import Digest, Text


class InputValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    @field_validator("*")
    @classmethod
    def canonical_json_numbers(cls, value: object) -> object:
        # JsonValue accepts nonfinite floats independently of the parent config.
        # Reject them before serialization can silently turn a RAW number null.
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, float) and not isfinite(item):
                raise ValueError("RAW numbers must be finite JSON values")
            if isinstance(item, dict):
                pending.extend(item.values())
            elif isinstance(item, list | tuple):
                pending.extend(item)
        return value


class BarSourceSlice(InputValue):
    start: AwareDatetime
    end: AwareDatetime
    rows: Annotated[tuple[dict[str, JsonValue], ...], Field(max_length=100000)]

    @model_validator(mode="after")
    def nonempty_interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("invalid source slice interval")
        return self


class BarNormalizationInput(InputValue):
    kind: Literal["bars"] = "bars"
    # Preserve each acquisition interval: a full-year response is cropped to
    # that slice, not merely to the parent's possibly cross-year range.
    slices: Annotated[tuple[BarSourceSlice, ...], Field(min_length=1, max_length=1000)]
    calendar_days: Annotated[tuple[date, ...], Field(max_length=10000)] | None
    fetch_mechanisms: Annotated[tuple[Text, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def complete_context(self) -> Self:
        if (
            (
                self.calendar_days is not None
                and len(set(self.calendar_days)) != len(self.calendar_days)
            )
            or len(set(self.fetch_mechanisms)) != len(self.fetch_mechanisms)
            or sum(len(item.rows) for item in self.slices) > 100000
            or any(
                left.end != right.start
                for left, right in zip(self.slices, self.slices[1:], strict=False)
            )
        ):
            raise ValueError("duplicate, incomplete or oversized conversion context")
        return self


class FactorNormalizationInput(InputValue):
    kind: Literal["factors"] = "factors"
    rows: Annotated[tuple[dict[str, JsonValue], ...], Field(max_length=365)]
    definition: FactorDefinition
    # JSON archival turns Decimal into exact text. Preserve numeric vs textual
    # source types separately; a numeric-looking signal must remain a signal.
    value_kinds: Annotated[
        tuple[Literal["number", "signal", "missing"], ...], Field(max_length=365)
    ]

    @model_validator(mode="after")
    def complete_types(self) -> Self:
        if len(self.rows) != len(self.value_kinds):
            raise ValueError("source value type count mismatch")
        return self


NormalizationPayload = Annotated[
    BarNormalizationInput | FactorNormalizationInput, Field(discriminator="kind")
]


class CollectionNormalizationInput(InputValue):
    schema_version: Literal[2] = 2
    format_version: Literal["collection-normalization-input.v1"] = (
        "collection-normalization-input.v1"
    )
    input_id: UUID
    request: CollectionJobRequest
    job_id: UUID
    part_id: UUID
    instrument_id: Text
    field: Text | None
    start: AwareDatetime
    end: AwareDatetime
    mapping_version: Text
    instrument_name: Text
    raw_capture_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=4096)]
    payload: NormalizationPayload
    prepared_at: AwareDatetime
    input_sha256: Digest

    @field_validator("schema_version", mode="before")
    @classmethod
    def version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("unsupported checkpoint schema")
        return value

    @model_validator(mode="after")
    def bound_input(self) -> Self:
        if (
            self.job_id != self.request.request_id
            or self.instrument_id not in self.request.instrument_ids
            or not self.request.start <= self.start < self.end <= self.request.end
            or self.payload.kind != self.request.dataset_kind
            or len(set(self.raw_capture_ids)) != len(self.raw_capture_ids)
        ):
            raise ValueError("checkpoint selection mismatch")
        if isinstance(self.payload, FactorNormalizationInput):
            if (
                self.field != self.payload.definition.factor_id
                or self.field not in self.request.fields
            ):
                raise ValueError("checkpoint factor definition mismatch")
        elif (
            self.field is not None
            or self.payload.slices[0].start != self.start
            or self.payload.slices[-1].end != self.end
        ):
            raise ValueError("bar checkpoint interval/field mismatch")
        identity = self.model_dump(mode="json", exclude={"input_id", "input_sha256", "prepared_at"})
        if self.input_id != uuid5(self.part_id, digest(identity)):
            raise ValueError("checkpoint identity mismatch")
        if self.input_sha256 != digest(self.model_dump(mode="json", exclude={"input_sha256"})):
            raise ValueError("checkpoint checksum mismatch")
        return self
