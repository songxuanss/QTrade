"""Transport-neutral, immutable contracts; no QTrade business package dependencies."""

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
    model_validator,
)

Text = Annotated[str, Field(min_length=1, max_length=256)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Period = Literal["tick", "1m", "5m", "15m", "1h", "1d"]
BarPeriod = Literal["1m", "5m", "15m", "1h", "1d"]
ErrorCode = Literal[
    "invalid_input",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "unsupported",
    "unverified",
    "rate_limited",
    "timeout",
    "unavailable",
]


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise ValueError("decimal values require plain decimal strings, integers or Decimal")
    if isinstance(value, str) and (
        len(value) > 80 or not re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", value)
    ):
        raise ValueError("invalid decimal text")
    result = Decimal(value)
    exponent = result.as_tuple().exponent
    if (
        not result.is_finite()
        or len(result.as_tuple().digits) > 38
        or not isinstance(exponent, int)
        or not -18 <= exponent <= 18
    ):
        raise ValueError("decimal exceeds precision bounds")
    return result


ExactDecimal = Annotated[
    Decimal,
    BeforeValidator(_decimal),
    PlainSerializer(lambda value: format(value, "f")),
    WithJsonSchema({"type": "string", "pattern": r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$"}),
]
NonNegative = Annotated[ExactDecimal, Field(ge=0)]
Positive = Annotated[ExactDecimal, Field(gt=0)]


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


class DataContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1]
    scope_id: UUID

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported data contract version")
        return value

    @field_validator("*", mode="after")
    @classmethod
    def utc_time(cls, value: Any) -> Any:
        return _utc(value) if isinstance(value, datetime) else value


class ReferenceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument_id: Text
    instrument_kind: Literal["stock", "index"]
    name: Text
    exchange: Text
    currency: Literal["CNY"] = "CNY"

    @model_validator(mode="after")
    def distinct_identity(self) -> Self:
        if self.instrument_kind == "index" and not self.instrument_id.startswith("index:"):
            raise ValueError("index identity requires index namespace")
        if self.instrument_kind == "stock" and self.instrument_id.startswith("index:"):
            raise ValueError("stock cannot use index namespace")
        return self


class UniverseMember(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument: ReferenceIdentity
    affiliations: tuple[Text, ...] = ()
    classification_evidence: tuple[Text, ...] = ()
    market_cap: NonNegative | None = None
    market_cap_basis: Literal["provider_total", "provider_float"] | None = None
    valuation_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valuation(self) -> Self:
        if (self.market_cap is None) != (self.market_cap_basis is None):
            raise ValueError("market cap and basis must be paired")
        if self.market_cap is not None and self.valuation_at is None:
            raise ValueError("valuation time required")
        if self.valuation_at is not None and self.valuation_at.utcoffset() is None:
            raise ValueError("aware valuation time required")
        if len(set(self.affiliations)) != len(self.affiliations):
            raise ValueError("duplicate affiliations")
        return self

    @field_validator("valuation_at")
    @classmethod
    def valuation_utc(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value else None


class ReferenceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument_id: Text
    code: Literal["directory_member_missing", "name_conflict"]
    directory_name: Text | None = None
    member_name: Text


class UniverseDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    universe_id: Text
    name: Text
    category: Literal["industry", "theme", "index_members"]
    source: Text
    source_code: Text
    parent_code: Text | None = None
    source_status: Literal["available", "ambiguous"] = "available"


class UniverseContent(DataContract):
    universe_id: Text
    version: Annotated[int, Field(ge=1)]
    name: Text
    category: Literal["custom", "index_members", "industry", "theme", "market_cap"]
    source: Text
    observed_at: AwareDatetime
    membership_basis: Literal["current_snapshot", "verified_historical"]
    effective_start: AwareDatetime | None = None
    effective_end: AwareDatetime | None = None
    market_scope: tuple[Text, ...]
    classification_version: Text
    members: Annotated[tuple[UniverseMember, ...], Field(min_length=1, max_length=10000)]
    reference_issues: tuple[ReferenceIssue, ...] = ()

    @model_validator(mode="after")
    def snapshot_invariants(self) -> Self:
        ids = tuple(member.instrument.instrument_id for member in self.members)
        if len(set(ids)) != len(ids):
            raise ValueError("snapshot members must be unique")
        if not self.market_scope or len(set(self.market_scope)) != len(self.market_scope):
            raise ValueError("market scope must be explicit and unique")
        historical = self.membership_basis == "verified_historical"
        if historical and (self.effective_start is None or self.effective_end is None):
            raise ValueError("historical membership requires effective range")
        if not historical and (self.effective_start is not None or self.effective_end is not None):
            raise ValueError("current snapshot cannot claim historical interval")
        if (
            self.effective_start is not None
            and self.effective_end is not None
            and self.effective_start >= self.effective_end
        ):
            raise ValueError("invalid membership range")
        return self


class UniverseSnapshot(UniverseContent):
    snapshot_id: UUID
    content_sha256: Digest

    @model_validator(mode="after")
    def valid_hash(self) -> Self:
        if self.content_sha256 != self.calculate_hash():
            raise ValueError("snapshot content hash mismatch")
        return self

    @classmethod
    def freeze(cls, snapshot_id: UUID, content: UniverseContent) -> Self:
        payload = content.model_dump(mode="json")
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        return cls(**content.model_dump(), snapshot_id=snapshot_id, content_sha256=digest)

    def calculate_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"content_sha256", "snapshot_id"})
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()


class CollectionSelection(DataContract):
    request_id: UUID
    actor_id: UUID
    correlation_id: UUID
    snapshot_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=100)]
    instrument_ids: tuple[Text, ...] = ()
    source: Text
    authorization_id: Text
    granularity: Period
    start: AwareDatetime
    end: AwareDatetime
    mode: Literal["missing", "all"] = "missing"
    transformation_version: Text

    @model_validator(mode="after")
    def selection(self) -> Self:
        if (
            self.start >= self.end
            or len(set(self.snapshot_ids)) != len(self.snapshot_ids)
            or len(set(self.instrument_ids)) != len(self.instrument_ids)
        ):
            raise ValueError("invalid selection interval or repeated snapshot")
        return self


class SourceCapability(DataContract):
    source: Text
    authorization_id: Text
    instrument_kinds: tuple[Literal["stock", "index"], ...]
    granularities: tuple[Period, ...]
    earliest: AwareDatetime | None = None
    latest: AwareDatetime | None = None
    verification: Literal["verified", "unverified", "unavailable"]
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def capability(self) -> Self:
        if not self.instrument_kinds or not self.granularities:
            raise ValueError("empty capability")
        if len(set(self.granularities)) != len(self.granularities):
            raise ValueError("duplicate periods")
        if self.earliest and self.latest and self.earliest >= self.latest:
            raise ValueError("invalid source range")
        return self


class CatalogQuery(DataContract):
    instrument_id: Text
    granularity: BarPeriod
    start: AwareDatetime
    end: AwareDatetime
    dataset_id: UUID | None = None
    limit: Annotated[int, Field(ge=1, le=10000)] = 1000
    cursor: Annotated[str, Field(min_length=1, max_length=2048)] | None = None

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.start >= self.end:
            raise ValueError("invalid query interval")
        return self


class DataBar(DataContract):
    dataset_id: UUID
    instrument_id: Text
    granularity: BarPeriod
    timestamp: AwareDatetime
    open: Positive
    high: Positive
    low: Positive
    close: Positive
    volume: NonNegative
    batch_ids: Annotated[tuple[UUID, ...], Field(min_length=1)]
    quality: Literal["verified", "warning", "unverified"]

    @model_validator(mode="after")
    def ohlc(self) -> Self:
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("invalid OHLC envelope")
        return self


class BarPage(DataContract):
    bars: Annotated[tuple[DataBar, ...], Field(max_length=10000)]
    next_cursor: Annotated[str, Field(min_length=1, max_length=2048)] | None = None

    @model_validator(mode="after")
    def scoped(self) -> Self:
        if any(bar.scope_id != self.scope_id for bar in self.bars):
            raise ValueError("bar page scope mismatch")
        return self


class PublicationManifest(DataContract):
    dataset_id: UUID
    dataset_version: Annotated[int, Field(ge=1)]
    source: Text
    instrument_ids: Annotated[tuple[Text, ...], Field(min_length=1)]
    granularity: BarPeriod
    start: AwareDatetime
    end: AwareDatetime
    record_count: Annotated[int, Field(ge=1)]
    batch_ids: Annotated[tuple[UUID, ...], Field(min_length=1)]
    content_sha256: Digest
    transformation_version: Text
    published_at: AwareDatetime
    quality: Literal["verified", "warning", "unverified"]

    @model_validator(mode="after")
    def published(self) -> Self:
        if self.start >= self.end or len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("invalid publication range or duplicate members")
        return self


class CollectionRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    start: AwareDatetime
    end: AwareDatetime

    @field_validator("start", "end")
    @classmethod
    def normalized(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def range_order(self) -> Self:
        if self.start >= self.end:
            raise ValueError("range must be nonempty")
        return self


class MemberCollectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument: ReferenceIdentity
    affiliations: tuple[Text, ...]
    existing_ranges: tuple[CollectionRange, ...]
    submit_ranges: tuple[CollectionRange, ...]
    planned_parts: Annotated[int, Field(ge=0)]
    reused_task_ids: tuple[UUID, ...] = ()
    completed_task_ids: tuple[UUID, ...] = ()


class CampaignPlan(DataContract):
    plan_id: UUID
    selection: CollectionSelection
    coverage_version: Digest
    created_at: AwareDatetime
    expires_at: AwareDatetime
    members: Annotated[tuple[MemberCollectionPlan, ...], Field(min_length=1, max_length=10000)]
    planned_parts: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def plan_invariants(self) -> Self:
        if self.selection.scope_id != self.scope_id or self.expires_at <= self.created_at:
            raise ValueError("invalid plan scope or expiry")
        if len({m.instrument.instrument_id for m in self.members}) != len(self.members):
            raise ValueError("duplicate planned members")
        total = 0
        for member in self.members:
            ranges = member.submit_ranges
            if any(r.start < self.selection.start or r.end > self.selection.end for r in ranges):
                raise ValueError("planned range outside selection")
            if any(left.end > right.start for left, right in zip(ranges, ranges[1:], strict=False)):
                raise ValueError("planned ranges overlap or are unsorted")
            parts = sum(
                (r.end - r.start) // timedelta(days=7) + bool((r.end - r.start) % timedelta(days=7))
                for r in ranges
            )
            parts += len(member.reused_task_ids)
            if (
                len(set(member.reused_task_ids)) != len(member.reused_task_ids)
                or len(set(member.completed_task_ids)) != len(member.completed_task_ids)
                or set(member.completed_task_ids) & set(member.reused_task_ids)
            ):
                raise ValueError("duplicate referenced work")
            if member.planned_parts != parts:
                raise ValueError("member parts do not match seven-day ranges")
            total += parts
        if self.planned_parts != total:
            raise ValueError("planned parts mismatch")
        return self


class CampaignProgress(DataContract):
    campaign_id: UUID
    plan_id: UUID
    version: Annotated[int, Field(ge=1)]
    state: Literal["queued", "running", "partial", "completed", "failed", "cancelled"]
    planned_parts: Annotated[int, Field(ge=0)]
    expanded_parts: Annotated[int, Field(ge=0)]
    collected_parts: Annotated[int, Field(ge=0)]
    failed_parts: Annotated[int, Field(ge=0)]
    member_count: Annotated[int, Field(ge=1)]
    viewable_members: Annotated[int, Field(ge=0)]
    no_observation_members: Annotated[int, Field(ge=0)]
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def counts(self) -> Self:
        if (
            not self.collected_parts + self.failed_parts
            <= self.expanded_parts
            <= self.planned_parts
        ):
            raise ValueError("invalid expanded/terminal counts")
        if self.viewable_members + self.no_observation_members > self.member_count:
            raise ValueError("invalid member result counts")
        if self.state == "completed" and (
            self.collected_parts != self.planned_parts
            or self.failed_parts > 0
            or self.viewable_members != self.member_count
        ):
            raise ValueError("completion requires all work and all nonempty published members")
        return self


class DataError(DataContract):
    code: ErrorCode
    correlation_id: UUID
    retry_after_seconds: Annotated[int, Field(ge=0, le=86400)] | None = None


CONTRACTS = (
    UniverseSnapshot,
    CollectionSelection,
    CampaignPlan,
    CampaignProgress,
    SourceCapability,
    CatalogQuery,
    DataBar,
    BarPage,
    PublicationManifest,
    DataError,
)
