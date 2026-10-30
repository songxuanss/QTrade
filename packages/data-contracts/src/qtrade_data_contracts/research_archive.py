"""Immutable v2 evidence manifests; publication state is separate from archive existence."""

from datetime import date
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .collection import check_instrument
from .models import Digest, Text
from .research import ResearchV2
from .research_transport import ResearchCapability, ResearchKind


class ResearchRawManifest(ResearchV2):
    scope_id: UUID
    request_id: UUID
    batch_id: UUID
    plan_sha256: Digest
    source: Text
    kind: ResearchKind
    instrument_id: Text
    start: date
    end: date
    cursor: Text | None
    next_cursor: Text | None
    collected_at: AwareDatetime
    raw_sha256: Digest
    raw_content_sha256: Digest
    raw_record_count: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def valid_raw(self) -> Self:
        check_instrument(self.instrument_id)
        if self.start >= self.end:
            raise ValueError("raw interval must be nonempty")
        if self.next_cursor is not None and self.next_cursor == self.cursor:
            raise ValueError("raw cursor must advance")
        return self


class ResearchArtifactManifest(ResearchV2):
    scope_id: UUID
    request_id: UUID
    artifact_id: UUID
    plan_sha256: Digest
    capability: ResearchCapability
    mapping_version: Text
    raw_batches: Annotated[tuple[ResearchRawManifest, ...], Field(min_length=1)]
    records_sha256: Digest
    records_file_sha256: Digest
    semantic_sha256: Digest
    record_count: Annotated[int, Field(ge=1)]
    created_at: AwareDatetime

    @model_validator(mode="after")
    def consistent_lineage(self) -> Self:
        if self.mapping_version != self.capability.mapping_version:
            raise ValueError("artifact mapping version mismatch")
        if len({b.batch_id for b in self.raw_batches}) != len(self.raw_batches):
            raise ValueError("duplicate raw lineage")
        if any(
            b.scope_id != self.scope_id
            or b.request_id != self.request_id
            or b.plan_sha256 != self.plan_sha256
            or b.source != self.capability.source
            or b.kind != self.capability.kind
            for b in self.raw_batches
        ):
            raise ValueError("artifact lineage disagrees with frozen scope/plan/source")
        return self
