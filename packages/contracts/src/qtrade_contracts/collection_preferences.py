"""Typed workspace draft, separate from executable independent collection requests."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CollectionPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
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
