"""Versioned remote collection/publication seam, independent of any trading application."""

import json
import re
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)

from .models import DataContract, Digest, ExactDecimal, NonNegative, Positive, Text
from .planning import CollectionCostEstimate, CollectionPlanGuard

CollectionPeriod = Literal["1m", "5m", "15m", "30m", "1h", "1d"]
DatasetKind = Literal["bars", "factors"]
AssetKind = Literal["stock", "index", "etf", "future", "crypto"]
RawPayloadKind = Literal["calendar", "history", "cached_history", "factor_history", "http_response"]


class CollectionRawCapture(DataContract):
    """An acquisition receipt is not a publication or proof of price coverage."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    capture_id: UUID
    scope_id: UUID
    part_id: UUID
    job_id: UUID
    actor_id: UUID
    source: Text
    mapping_version: Text
    kind: RawPayloadKind
    attempt: Annotated[int, Field(ge=0)]
    lease_token: UUID | None
    content_sha256: Digest
    captured_at: AwareDatetime
    response_rows: Annotated[int, Field(ge=0)] | None


class InstrumentIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument_id: Text
    asset_kind: AssetKind
    market: Text
    venue: Text
    code: Text
    name: Text
    currency: Text
    product_type: Literal["cash", "dated", "continuous", "spot", "perpetual"] = "cash"

    @model_validator(mode="after")
    def consistent_identity(self) -> Self:
        if instrument_key(self.instrument_id) != (
            self.asset_kind,
            self.market,
            self.venue,
            self.code,
        ):
            raise ValueError("identity namespace and attributes disagree")
        if self.asset_kind == "future":
            if self.product_type not in {"dated", "continuous"}:
                raise ValueError("future requires explicit contract type")
            if (self.product_type == "continuous") != self.code.endswith("@CONT"):
                raise ValueError("continuous futures require a distinct product code")
        elif self.asset_kind == "crypto":
            if self.product_type not in {"spot", "perpetual"}:
                raise ValueError("crypto requires explicit product type")
            if (self.product_type == "perpetual") != self.code.endswith("@PERP"):
                raise ValueError("perpetual contracts require a distinct product code")
        elif self.product_type != "cash":
            raise ValueError("cash asset product type required")
        return self


def instrument_key(value: str) -> tuple[str, str, str, str]:
    legacy = re.fullmatch(r"(index:)?([0-9]{6})\.(SH|SZ|BJ)", value)
    if legacy:
        return ("index" if legacy[1] else "stock", "CN", legacy[3], legacy[2])
    canonical = re.fullmatch(
        r"(stock|index|etf|future|crypto):([A-Z]{2,12}):([A-Z0-9_]{2,20}):([A-Za-z0-9_.@/-]{1,80})",
        value,
    )
    if not canonical:
        raise ValueError("explicit market, venue and asset identity required")
    return canonical[1], canonical[2], canonical[3], canonical[4]


def digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def check_instrument(value: str) -> None:
    instrument_key(value)


class FactorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    factor_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_]{1,80}$")]
    name: Text
    category: Text
    unit: str
    description: str
    calc_version: Text
    update_frequency: Text
    available: bool
    unavailable_reason: str | None = None


class SourceDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    source: Text
    provider: Text
    name: Text
    dataset_kind: DatasetKind
    periods: tuple[CollectionPeriod, ...]
    instrument_kinds: tuple[AssetKind, ...]
    markets: tuple[Text, ...]
    timezone: Text = "UTC"
    configured: bool
    evidence: Literal["documented", "configured", "sample_verified", "e2e_verified", "blocked"]
    mapping_version: Text
    earliest: AwareDatetime | None = None
    limitations: tuple[str, ...] = ()
    # Commercial tier drives the workbench default ("prefer an available paid
    # source, fall back to free"). Vendor-neutral: adapters classify their own
    # datasets; consumers must not infer tier from source-name patterns.
    tier: Literal["paid", "free"] = "free"


class CollectionDefaultRange(BaseModel):
    """Source-calendar estimate; availability must still be verified at collection."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    source: Text
    granularity: CollectionPeriod
    first: date
    last: date
    start: AwareDatetime
    end: AwareDatetime
    timezone: Text
    expected_available_at: AwareDatetime
    basis: Text
    endpoint_semantics: Literal["start_inclusive_end_exclusive"] = "start_inclusive_end_exclusive"
    availability: Literal["estimated_not_verified"] = "estimated_not_verified"

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.start >= self.end or self.first > self.last:
            raise ValueError("invalid default range")
        return self


class CollectionJobRequest(DataContract):
    request_id: UUID
    actor_id: UUID
    correlation_id: UUID
    source: Text
    dataset_kind: DatasetKind
    instrument_ids: Annotated[tuple[Text, ...], Field(min_length=1, max_length=10000)]
    granularity: CollectionPeriod
    start: AwareDatetime
    end: AwareDatetime
    fields: Annotated[tuple[str, ...], Field(max_length=200)] = ()
    mode: Literal["missing", "all"] = "missing"

    @model_validator(mode="after")
    def valid_request(self) -> Self:
        if self.start >= self.end or len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("invalid range or duplicate instruments")
        for value in self.instrument_ids:
            check_instrument(value)
        if len(set(self.fields)) != len(self.fields) or any(
            not re.fullmatch(r"[a-zA-Z0-9_]{1,80}", value) for value in self.fields
        ):
            raise ValueError("invalid field selection")
        if self.dataset_kind == "factors" and (self.granularity != "1d" or not self.fields):
            raise ValueError("factor history requires daily period and explicit fields")
        if self.dataset_kind == "bars" and self.fields:
            raise ValueError("bar fields are defined by the contract")
        return self


def collection_wire_request(value: object) -> CollectionJobRequest:
    """Retain strict JSON semantics after an HTTP framework decodes a request dictionary."""
    if isinstance(value, CollectionJobRequest):
        return value
    return CollectionJobRequest.model_validate_json(json.dumps(value))


CollectionWireRequest = Annotated[CollectionJobRequest, BeforeValidator(collection_wire_request)]


class CollectionPreview(DataContract):
    request: CollectionJobRequest
    mapping_version: Text
    coverage_version: Digest
    planned_parts: Annotated[int, Field(ge=0)]
    completed_parts: Annotated[int, Field(ge=0)]
    active_parts: Annotated[int, Field(ge=0)]
    missing_parts: Annotated[int, Field(ge=0)]
    field_definitions: tuple[FactorDefinition, ...] = ()
    limitations: tuple[str, ...] = ()
    instrument_names: dict[str, str] = {}
    # Mutable previews evolve additively; old immutable publications are untouched.
    execution_estimate: "CollectionCostEstimate | None" = None
    plan_guard: "CollectionPlanGuard | None" = None
    confirmation_required: bool = False


class CollectedBar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument_id: Text
    timestamp: AwareDatetime
    granularity: CollectionPeriod
    open: Positive
    high: Positive
    low: Positive
    close: Positive
    volume: NonNegative
    value: NonNegative | None
    quality: Literal["valid", "suspect"] = "valid"

    @model_validator(mode="after")
    def valid_bar(self) -> Self:
        check_instrument(self.instrument_id)
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("invalid OHLC envelope")
        return self


_FACTOR_DECIMAL = TypeAdapter(ExactDecimal)


class FactorPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    instrument_id: Text
    factor_id: Text
    trade_date: date
    value: ExactDecimal | str | None
    value_kind: Literal["number", "signal", "missing"]
    missing_reason: Literal["provider_null", "not_available"] | None = None
    calc_version: Text
    available_at: AwareDatetime | None = None
    availability_evidence: Literal["unknown", "provider_timestamp"] = "unknown"

    @field_validator("value", mode="wrap")
    @classmethod
    def keep_source_text(cls, value: object, handler: ValidatorFunctionWrapHandler) -> object:
        # Numeric-looking text may be a signal. Let value_kind decide numeric
        # intent after parsing, rather than the union's Decimal branch order.
        if isinstance(value, str):
            return value
        result: object = handler(value)
        return result

    @model_validator(mode="after")
    def valid_point(self) -> Self:
        check_instrument(self.instrument_id)
        if self.value_kind == "missing":
            if self.value is not None or self.missing_reason is None:
                raise ValueError("missing factor needs a reason")
        elif self.value is None or self.missing_reason is not None:
            raise ValueError("nonmissing factor needs a value")
        if self.value_kind == "number":
            if isinstance(self.value, str):
                try:
                    numeric = _FACTOR_DECIMAL.validate_python(self.value)
                except (ArithmeticError, ValueError):
                    raise ValueError("invalid numeric factor") from None
                if not numeric.is_finite():
                    raise ValueError("invalid numeric factor")
                object.__setattr__(self, "value", numeric)
            elif not isinstance(self.value, Decimal):
                raise ValueError("numeric factor requires exact decimal")
        if self.value_kind == "signal" and not isinstance(self.value, str):
            raise ValueError("signal factor requires text")
        if self.availability_evidence == "provider_timestamp" and self.available_at is None:
            raise ValueError("availability evidence requires a timestamp")
        return self


class CollectionPublication(DataContract):
    publication_id: UUID
    job_id: UUID
    part_id: UUID
    actor_id: UUID
    correlation_id: UUID
    source: Text
    dataset_kind: DatasetKind
    instrument_id: Text
    instrument_name: Text
    granularity: CollectionPeriod
    field: str | None = None
    start: AwareDatetime
    end: AwareDatetime
    mapping_version: Text
    raw_sha256: Digest
    records_sha256: Digest
    raw_record_count: Annotated[int, Field(ge=1)]
    record_count: Annotated[int, Field(ge=1)]
    published_at: AwareDatetime
    units: dict[str, str]
    source_time_convention: Text
    evidence: dict[str, object] = {}
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_publication(self) -> Self:
        check_instrument(self.instrument_id)
        if self.start >= self.end:
            raise ValueError("invalid publication range")
        if self.dataset_kind == "bars" and self.field is not None:
            raise ValueError("bar publication cannot have a factor field")
        if self.dataset_kind == "factors" and (not self.field or self.granularity != "1d"):
            raise ValueError("factor publication requires daily field")
        return self


class CollectionRecordPage(DataContract):
    publication_id: UUID
    records: Annotated[tuple[CollectedBar | FactorPoint, ...], Field(max_length=1000)]
    offset: Annotated[int, Field(ge=0)]
    next_offset: Annotated[int, Field(ge=0)] | None
    page_sha256: Digest

    @model_validator(mode="after")
    def valid_page(self) -> Self:
        if self.page_sha256 != digest([record.model_dump(mode="json") for record in self.records]):
            raise ValueError("record page checksum mismatch")
        return self
