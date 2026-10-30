"""Typed discovery, side-effect-free planning and immutable research page contracts."""

from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, model_validator

from .collection import AssetKind, check_instrument, digest
from .models import Digest, Text
from .research import ResearchObservation, ResearchV2

ResearchKind = Literal[
    "financial_statement",
    "membership_interval",
    "security_identity_change",
    "corporate_action",
    "adjustment_factor",
]
DateAxis = Literal["report_period", "observation_date", "effective_date", "event_date"]


class ResearchCapability(ResearchV2):
    source: Text
    name: Text
    kind: ResearchKind
    configured: bool
    evidence: Literal["unconfigured", "documented", "sample", "live_e2e", "fixture"]
    markets: Annotated[tuple[Text, ...], Field(min_length=1)]
    instrument_kinds: Annotated[tuple[AssetKind, ...], Field(min_length=1)]
    date_axes: Annotated[tuple[DateAxis, ...], Field(min_length=1)]
    timezone: Text
    mapping_version: Text
    history_start: date | None = None
    history_end: date | None = None
    supports_as_of: bool = False
    knowledge_evidence: Text | None = None
    limitations: tuple[Text, ...] = ()
    unblock_condition: Text | None = None

    @model_validator(mode="after")
    def coherent_capability(self) -> Self:
        ZoneInfo(self.timezone)
        if self.supports_as_of and self.knowledge_evidence is None:
            raise ValueError("as-of requires knowledge evidence")
        if not self.configured and self.unblock_condition is None:
            raise ValueError("disabled capability requires an unblock condition")
        if self.history_start and self.history_end and self.history_start >= self.history_end:
            raise ValueError("history interval must be nonempty and half-open")
        return self


class ResearchPlanRequest(ResearchV2):
    actor_id: UUID
    scope_id: UUID
    request_id: UUID
    correlation_id: UUID
    source: Text
    kind: ResearchKind
    instrument_ids: Annotated[tuple[Text, ...], Field(min_length=1, max_length=10000)]
    date_axis: DateAxis
    start: date
    end: date
    mode: Literal["missing", "all", "review"] = "missing"

    @model_validator(mode="after")
    def bounded_request(self) -> Self:
        if self.start >= self.end:
            raise ValueError("research interval must be nonempty and half-open")
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("duplicate instrument")
        for identifier in self.instrument_ids:
            check_instrument(identifier)
        return self


class ResearchPartition(ResearchV2):
    instrument_id: Text
    start: date
    end: date

    @model_validator(mode="after")
    def valid_partition(self) -> Self:
        check_instrument(self.instrument_id)
        if self.start >= self.end:
            raise ValueError("partition interval must be nonempty")
        return self


class ResearchPlan(ResearchV2):
    request: ResearchPlanRequest
    capability: ResearchCapability
    partitions: tuple[ResearchPartition, ...]
    plan_version: Text
    plan_sha256: Digest
    estimated_calls_min: Annotated[int, Field(ge=0)]
    estimated_calls_max: Annotated[int, Field(ge=0)] | None
    estimate_basis: Text

    @model_validator(mode="after")
    def valid_plan(self) -> Self:
        if (
            self.request.source != self.capability.source
            or self.request.kind != self.capability.kind
        ):
            raise ValueError("plan capability disagrees with selection")
        if (
            self.estimated_calls_max is not None
            and self.estimated_calls_max < self.estimated_calls_min
        ):
            raise ValueError("invalid call estimate interval")
        if self.plan_sha256 != digest(
            {
                "request": self.request.model_dump(mode="json"),
                "capability": self.capability.model_dump(mode="json"),
                "partitions": [p.model_dump(mode="json") for p in self.partitions],
                "plan_version": self.plan_version,
            }
        ):
            raise ValueError("plan checksum mismatch")
        return self


class ResearchQuery(ResearchV2):
    actor_id: UUID
    scope_id: UUID
    publication_id: UUID
    content_version: Digest
    kind: ResearchKind
    cursor: Text | None = None
    limit: Annotated[int, Field(ge=1, le=1000)] = 1000
    as_of: AwareDatetime | None = None


class ResearchRecordPage(ResearchV2):
    scope_id: UUID
    publication_id: UUID
    content_version: Digest
    kind: ResearchKind
    cursor: Text | None
    next_cursor: Text | None
    records: Annotated[tuple[ResearchObservation, ...], Field(max_length=1000)]
    page_sha256: Digest

    @model_validator(mode="after")
    def valid_page(self) -> Self:
        if any(
            record.scope_id != self.scope_id or record.kind != self.kind for record in self.records
        ):
            raise ValueError("page record scope or kind mismatch")
        if self.next_cursor is not None and self.next_cursor == self.cursor:
            raise ValueError("cursor must advance")
        if self.page_sha256 != digest([r.model_dump(mode="json") for r in self.records]):
            raise ValueError("page checksum mismatch")
        return self
