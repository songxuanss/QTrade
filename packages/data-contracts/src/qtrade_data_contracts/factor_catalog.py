"""Cached metadata evidence is independent of collection/history authorization."""

from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .collection import FactorDefinition, digest
from .models import DataContract, Digest, ErrorCode

CatalogStatus = Literal["fresh", "stale", "loading", "unavailable", "unconfigured"]


class FactorCatalogSnapshot(BaseModel):
    # Definitions are global supplier metadata. HTTP readers still authenticate
    # in their current scope; no user-specific holdings or rights live here.
    schema_version: Literal[1]
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    source: Literal["mairui.factor.history"]
    status: CatalogStatus
    definitions: tuple[FactorDefinition, ...] = Field(max_length=1000)
    content_sha256: Digest | None
    fetched_at: AwareDatetime | None
    last_attempt_at: AwareDatetime | None
    refreshing: bool
    error_code: ErrorCode | None
    permission_evidence: Literal["not_established_by_catalog"]

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_version(cls, value: Any) -> int:
        return DataContract.validate_schema_version(value)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.definitions:
            if self.status not in {"fresh", "stale"}:
                raise ValueError("cached definitions need a cache status")
            if self.content_sha256 != digest([d.model_dump(mode="json") for d in self.definitions]):
                raise ValueError("catalog digest mismatch")
            if len({d.factor_id for d in self.definitions}) != len(self.definitions):
                raise ValueError("duplicate factor identifiers")
        elif self.content_sha256 is not None or self.status in {"fresh", "stale"}:
            raise ValueError("empty catalog cannot be fresh or stale")
        if self.status == "fresh" and (self.fetched_at is None or self.error_code is not None):
            raise ValueError("fresh catalog requires successful dated retrieval")
        return self
