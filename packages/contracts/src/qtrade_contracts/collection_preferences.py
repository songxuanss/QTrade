"""Typed workspace draft, separate from executable independent collection requests."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CollectionDimensionDraft(BaseModel):
    """One card in the multi-dimension composition. A card is only a draft:
    preview and submit still produce and freeze ordinary collection requests."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    dimension_id: UUID
    dataset_kind: Literal["bars", "factors"] = "bars"
    source: Annotated[str, Field(max_length=100)] = ""
    granularity: Literal["1d", "1m", "5m", "15m", "30m", "1h"] = "1d"
    first: date | None = None
    last: date | None = None
    instruments: Annotated[str, Field(max_length=20000)] = ""
    snapshot_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    fields: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[a-zA-Z0-9_]{1,80}$")], ...],
        Field(max_length=200),
    ] = ()
    mode: Literal["missing", "all"] = "missing"


class CollectionPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    # v1 drafts stay readable; new drafts are written as v2 with dimensions.
    schema_version: Literal[1, 2] = 2
    dataset_kind: Literal["bars", "factors"] = "bars"
    source: Annotated[str, Field(max_length=100)] = ""
    granularity: Literal["1d", "1m", "5m", "15m", "30m", "1h"] = "1d"
    first: date | None = None
    last: date | None = None
    instruments: Annotated[str, Field(max_length=20000)] = ""
    # Retain the original single-pool field for persisted drafts; new drafts
    # can combine several industry/index snapshots with explicit companies.
    snapshot_id: UUID | None = None
    snapshot_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    fields: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[a-zA-Z0-9_]{1,80}$")], ...],
        Field(max_length=200),
    ] = ()
    mode: Literal["missing", "all"] = "missing"
    # Ordered multi-dimension cards, previewed and submitted in this order.
    dimensions: Annotated[tuple[CollectionDimensionDraft, ...], Field(max_length=20)] = ()

    @model_validator(mode="after")
    def unique_dimensions(self) -> "CollectionPreferences":
        if len({item.dimension_id for item in self.dimensions}) != len(self.dimensions):
            raise ValueError("duplicate dimension id")
        return self
