"""Read-only capability evidence; deliberately outside immutable download plans.

These dated statements describe bounded evidence, not the current account's
permission or a guarantee of complete history. Configuration is reported apart.
"""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import DataContract, Text

Aspect = Literal[
    "history", "units", "adjustment", "time", "updates", "permission", "quota", "license"
]
ASPECTS: tuple[Aspect, ...] = (
    "history",
    "units",
    "adjustment",
    "time",
    "updates",
    "permission",
    "quota",
    "license",
)


EvidenceLevel = Literal["unknown", "documented", "sample_verified", "e2e_verified"]
ProfileKind = Literal[
    "bars",
    "factors",
    "financial_statement",
    "corporate_action",
    "membership_interval",
    "security_identity_change",
    "adjustment_factor",
    "rights_terms",
    "current_membership",
]


class CapabilityFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    aspect: Aspect
    detail: Text
    evidence: EvidenceLevel
    reference: Annotated[str, Field(pattern=r"^docs/[a-zA-Z0-9_./-]+\.md$")]
    as_of: date
    unblock_condition: Text | None = None

    @model_validator(mode="after")
    def unknown_needs_next_step(self) -> Self:
        if ".." in self.reference.split("/"):
            raise ValueError("evidence reference must stay within docs")
        if self.evidence == "unknown" and not self.unblock_condition:
            raise ValueError("unknown capability needs an explicit verification condition")
        return self


class SourceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    source: Text
    name: Text
    kind: ProfileKind
    mapping_version: Text | None
    markets: tuple[Text, ...]
    periods: tuple[Text, ...]
    instrument_kinds: tuple[Text, ...]
    timezone: Text
    state: Literal["configured", "unconfigured", "not_connected"]
    facts: Annotated[tuple[CapabilityFact, ...], Field(min_length=8, max_length=8)]

    @model_validator(mode="after")
    def complete_profile(self) -> Self:
        if {v.aspect for v in self.facts} != set(ASPECTS):
            raise ValueError("each capability aspect must appear exactly once")
        if self.state != "not_connected" and self.mapping_version is None:
            raise ValueError("registered adapters need a mapping version")
        return self


class SourceProfileCatalog(DataContract):
    """Bounded discovery response, scoped by current authentication at each read."""

    profiles: Annotated[tuple[SourceProfile, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def unique_sources(self) -> Self:
        if len({p.source for p in self.profiles}) != len(self.profiles):
            raise ValueError("duplicate source profile")
        return self
