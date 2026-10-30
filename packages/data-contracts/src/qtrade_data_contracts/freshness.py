"""Scoped freshness evidence, separate from immutable dataset manifests."""

from datetime import date, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .collection import check_instrument
from .models import DataContract, Digest, Text

FreshnessStatus = Literal["current", "within_grace", "late", "unknown", "unavailable"]
FreshnessStage = Literal["none", "source", "collection", "publication", "unknown"]
FreshnessReason = Literal[
    "published_current",
    "source_behind",
    "collection_behind",
    "publication_behind",
    "source_unavailable",
    "rule_unknown",
    "source_unknown",
]
FreshnessAction = Literal[
    "none", "check_source", "collect_missing", "check_publication", "verify_rule"
]


class FreshnessKey(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    scope_id: UUID
    source: Text
    instrument_id: Text
    dataset_kind: Literal["bars", "factors", "financial_statement", "corporate_action"]
    field: Text | None = None
    period: Literal["1d"] | None = None
    adjustment: Literal["unadjusted", "forward", "backward"] | None
    date_axis: Literal["observation_date", "report_period", "event_date"]
    mapping_version: Text

    @model_validator(mode="after")
    def valid_selection(self) -> Self:
        check_instrument(self.instrument_id)
        if self.dataset_kind in {"bars", "factors"}:
            if self.period != "1d" or self.date_axis != "observation_date":
                raise ValueError("only daily observation freshness is defined")
            if (self.dataset_kind == "factors") != (self.field is not None):
                raise ValueError("factor freshness requires a field")
            if (self.dataset_kind == "bars") != (self.adjustment is not None):
                raise ValueError("bar freshness requires explicit adjustment semantics")
        elif self.period is not None or self.field is not None:
            raise ValueError("sparse research does not have a daily period")
        elif self.adjustment is not None:
            raise ValueError("research freshness cannot carry price adjustment")
        elif self.date_axis != (
            "report_period" if self.dataset_kind == "financial_statement" else "event_date"
        ):
            raise ValueError("research date axis mismatch")
        return self


class FreshnessExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    complete_through: date
    available_at: AwareDatetime
    timezone: Text
    rule_version: Text
    basis: Text
    grace_seconds: Annotated[int, Field(ge=0, le=604800)] = 0

    @model_validator(mode="after")
    def valid_zone(self) -> Self:
        try:
            zone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError("known market timezone required") from None
        if self.complete_through > self.available_at.astimezone(zone).date():
            raise ValueError("expected completed period cannot follow its release")
        return self


class FreshnessCheckpoint(BaseModel):
    """Verified complete period, not the maximum timestamp in an arbitrary page.

    The producing adapter must verify the applicable coverage/calendar rules and
    attach a stable evidence digest. For sparse categories this means their own
    release rule; absence of an event is not proof of a missing daily bar.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    complete_through: date
    observed_at: AwareDatetime
    evidence_sha256: Digest


class FreshnessEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    key: FreshnessKey
    source_health: Literal["available", "unavailable", "unknown"]
    source: FreshnessCheckpoint | None = None
    collected: FreshnessCheckpoint | None = None
    published: FreshnessCheckpoint | None = None


class FreshnessHistoryQuery(BaseModel):
    """Read selection; a cursor never authorizes another scope or writes state."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    key: FreshnessKey
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    before: AwareDatetime | None = None


class FreshnessCheckRequest(BaseModel):
    """Locate trusted evidence; clients cannot submit health, clocks or scope."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    publication_id: UUID


class FreshnessResult(DataContract):
    key: FreshnessKey
    checked_at: AwareDatetime
    expectation: FreshnessExpectation | None
    evidence: FreshnessEvidence
    status: FreshnessStatus
    stage: FreshnessStage
    reason: FreshnessReason
    overdue_seconds: Annotated[int, Field(ge=0)]
    action: FreshnessAction

    @model_validator(mode="after")
    def scoped_result(self) -> Self:
        if self.scope_id != self.key.scope_id or self.evidence.key != self.key:
            raise ValueError("freshness evidence scope or identity mismatch")
        expected = {
            "published_current": ("none", "none", {"current"}),
            "source_behind": ("source", "check_source", {"late", "within_grace"}),
            "collection_behind": ("collection", "collect_missing", {"late", "within_grace"}),
            "publication_behind": ("publication", "check_publication", {"late", "within_grace"}),
            "source_unavailable": ("source", "check_source", {"unavailable"}),
            "rule_unknown": ("unknown", "verify_rule", {"unknown"}),
            "source_unknown": ("unknown", "check_source", {"unknown"}),
        }[self.reason]
        if (self.stage, self.action) != expected[:2] or self.status not in expected[2]:
            raise ValueError("freshness state and recovery action disagree")
        if (self.reason == "source_unavailable") != (self.evidence.source_health == "unavailable"):
            raise ValueError("unavailable source cannot be presented as current")
        if self.reason == "rule_unknown" and self.expectation is not None:
            raise ValueError("unknown rule cannot carry an expectation")
        if self.reason not in {"rule_unknown", "source_unavailable"} and self.expectation is None:
            raise ValueError("expected release evidence required")
        if self.status != "late" and self.overdue_seconds:
            raise ValueError("unknown or grace state cannot invent overdue duration")
        points = (self.evidence.source, self.evidence.collected, self.evidence.published)
        if any(point and point.observed_at > self.checked_at for point in points):
            raise ValueError("checkpoint cannot come from the future")
        if self.expectation is not None:
            if self.expectation.available_at > self.checked_at:
                raise ValueError("release expectation must already be due")
            zone = ZoneInfo(self.expectation.timezone)
            if any(
                point and point.complete_through > point.observed_at.astimezone(zone).date()
                for point in points
            ):
                raise ValueError("completed period cannot follow its observation")
            cutoff = self.expectation.complete_through
            source, collected, published = (
                bool(point and point.complete_through >= cutoff) for point in points
            )
            valid = {
                "published_current": published,
                "source_behind": self.evidence.source is not None and not source and not published,
                "collection_behind": source and not collected and not published,
                "publication_behind": source and collected and not published,
                "source_unknown": self.evidence.source is None and not published,
                "source_unavailable": True,
            }.get(self.reason, False)
            if not valid:
                raise ValueError("freshness decision lacks its declared coverage evidence")
            if self.status in {"late", "within_grace"}:
                release = self.expectation.available_at + timedelta(
                    seconds=self.expectation.grace_seconds
                )
                overdue = max(0, int((self.checked_at - release).total_seconds()))
                if (self.status == "within_grace") != (self.checked_at < release):
                    raise ValueError("freshness grace state disagrees with check clock")
                if overdue != self.overdue_seconds:
                    raise ValueError("freshness overdue duration disagrees with evidence")
        return self
