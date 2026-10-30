"""Preview execution estimate, not immutable dataset semantics or vendor pricing."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CollectionCostEstimate(BaseModel):
    """Uncached execution upper bound before retries, expressed in internal credit.

    Preview-time metadata is excluded; zero is a legitimate lower bound when
    every needed source response is cached or every partition can be reused.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    calls_min: int = Field(ge=0)
    calls_max: int = Field(ge=0)
    credits_min: int = Field(ge=0)
    credits_max: int = Field(ge=0)
    accounting: Literal["internal_credit_safeguard"]
    basis: str = Field(min_length=1, max_length=1000)
    includes_retries: Literal[False]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.calls_min > self.calls_max or self.credits_min > self.credits_max:
            raise ValueError("invalid estimate interval")
        return self


class CollectionPlanGuard(BaseModel):
    """Operator limits for new plans, independent of provider history boundaries.

    Confirmation thresholds prompt acknowledgement; positive hard limits reject
    the entire request. Neither changes previously frozen jobs or publications.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Literal["collection-plan-guard.v1"]
    max_instruments: int = Field(ge=1, le=10000)
    max_new_parts: int = Field(ge=1, le=1000000)
    confirm_new_parts: int = Field(ge=1, le=1000000)
    confirm_credits: int = Field(ge=1, le=1000000000)
