"""Versioned bar/factor decisions, distinct from legacy publication manifests."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .models import DataContract, Digest

IssueCode = Literal[
    "raw_missing",
    "record_invalid",
    "type_mismatch",
    "identity_mismatch",
    "period_mismatch",
    "range_mismatch",
    "duplicate",
    "unit_mismatch",
    "time_label_unknown",
    "source_suspect",
    "calendar_unknown",
    "calendar_invalid",
    "missing_trade_dates",
    "intraday_completeness_unknown",
    "factor_definition_unknown",
    "factor_unit_unknown",
    "factor_null",
    "knowledge_unknown",
    "future_knowledge",
]


class CollectionQualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    code: IssueCode
    severity: Literal["block", "warning"]
    affected_records: Annotated[int, Field(ge=1)]


class CollectionQualityResult(DataContract):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    quality_id: UUID
    scope_id: UUID
    part_id: UUID
    rule_version: Literal["collection-quality.v1"]
    input_digest: Digest
    records_sha256: Digest
    record_count: Annotated[int, Field(ge=0)]
    checked_at: AwareDatetime
    status: Literal["passed", "warning", "blocked", "no_observations"]
    issues: tuple[CollectionQualityIssue, ...]

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        blocked = any(issue.severity == "block" for issue in self.issues)
        expected = (
            "blocked"
            if blocked
            else "no_observations"
            if not self.record_count
            else "warning"
            if self.issues
            else "passed"
        )
        if self.status != expected or len({issue.code for issue in self.issues}) != len(
            self.issues
        ):
            raise ValueError("quality status or issue aggregation inconsistent")
        return self
