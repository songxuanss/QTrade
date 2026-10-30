"""Normalization rejection evidence, distinct from a completed quality evaluation."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .models import DataContract, Text


class CollectionProcessingFailure(DataContract):
    failure_id: UUID
    scope_id: UUID
    part_id: UUID
    job_id: UUID
    attempt: Annotated[int, Field(ge=1)]
    checker_version: Literal["collection-normalization-check.v1"]
    mapping_version: Text
    stage: Literal["normalization"]
    error_code: Literal["unverified"]
    status: Literal["blocked"]
    normalized_record_count: None = None
    raw_capture_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=4096)]
    failed_at: AwareDatetime

    @model_validator(mode="after")
    def unique_receipts(self) -> Self:
        if len(set(self.raw_capture_ids)) != len(self.raw_capture_ids):
            raise ValueError("duplicate raw receipt reference")
        return self
