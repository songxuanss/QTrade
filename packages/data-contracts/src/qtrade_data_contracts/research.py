"""V2 research records. Missing knowledge, dates and native units stay explicit.

This module does not upgrade ordinary historical reports into PIT observations.
Legacy v1 collection models remain unchanged.
"""

from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .collection import check_instrument, digest
from .models import ExactDecimal, NonNegative, Positive, Text


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class KnowledgeTime(StrictRecord):
    status: Literal["unknown", "evidenced"] = "unknown"
    known_at: AwareDatetime | None = None
    evidence_reference: Text | None = None

    @model_validator(mode="after")
    def evidence_required(self) -> Self:
        if self.status == "evidenced":
            if self.known_at is None or self.evidence_reference is None:
                raise ValueError("knowledge time requires a timestamp and evidence")
        elif self.known_at is not None or self.evidence_reference is not None:
            raise ValueError("unknown knowledge time cannot carry an invented timestamp")
        return self


class UnitEvidence(StrictRecord):
    unit: Text | None = None
    basis: Literal["unknown", "documented", "provider", "local_definition"] = "unknown"
    evidence_reference: Text | None = None

    @model_validator(mode="after")
    def explicit_basis(self) -> Self:
        if self.basis == "unknown":
            if self.unit is not None or self.evidence_reference is not None:
                raise ValueError("unknown unit must remain unknown")
        elif self.unit is None or self.evidence_reference is None:
            raise ValueError("known unit requires evidence")
        return self


class FinancialMetric(StrictRecord):
    field_id: Text
    name: Text
    original_value: ExactDecimal | None
    original_unit: UnitEvidence
    standard_value: ExactDecimal | None
    standard_unit: UnitEvidence
    missing_reason: Literal["provider_null", "not_reported", "not_authorized"] | None = None
    normalization: Literal["identity", "converted", "unresolved"]
    conversion_version: Text | None = None

    @model_validator(mode="after")
    def coherent_values(self) -> Self:
        if (self.original_value is None) != (self.missing_reason is not None):
            raise ValueError("missing original value requires an explicit reason")
        if self.original_value is None and self.standard_value is not None:
            raise ValueError("missing original value cannot become a standard value")
        if self.normalization == "unresolved":
            if self.standard_value is not None or self.standard_unit.basis != "unknown":
                raise ValueError("unresolved normalization cannot claim a standard value/unit")
        else:
            if self.standard_unit.basis == "unknown" or self.original_unit.basis == "unknown":
                raise ValueError("normalization requires evidenced units")
            if self.original_value is not None and self.standard_value is None:
                raise ValueError("normalized value is missing")
            if self.normalization == "identity" and (
                self.standard_value != self.original_value
                or self.standard_unit.unit != self.original_unit.unit
            ):
                raise ValueError("identity normalization must preserve value and unit")
            if self.normalization == "converted" and self.conversion_version is None:
                raise ValueError("conversion requires a version")
        return self


class ResearchV2(StrictRecord):
    schema_version: Literal[2] = 2

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("unsupported research schema version")
        return value


class ResearchRecord(ResearchV2):
    scope_id: UUID
    source: Text
    instrument_id: Text
    collected_at: AwareDatetime
    knowledge: KnowledgeTime = KnowledgeTime()
    source_revision: Text | None = None
    mapping_version: Text

    @model_validator(mode="after")
    def valid_identity(self) -> Self:
        check_instrument(self.instrument_id)
        return self

    @property
    def content_version(self) -> str:
        # Recollection is not a revision. Semantic timestamps and evidence ARE.
        return digest(self.model_dump(mode="json", exclude={"collected_at"}))


class FinancialStatement(ResearchRecord):
    kind: Literal["financial_statement"] = "financial_statement"
    statement: Literal["balance", "income", "cashflow"]
    report_period: date
    disclosure_date: date | None = None
    disclosure_precision: Literal["day", "unknown"] = "unknown"
    period_semantics: Literal["point_in_time", "year_to_date", "single_period", "unknown"]
    period_semantics_evidence: Text | None = None
    accounting_scope: Literal["consolidated", "parent", "unknown"] = "unknown"
    metrics: Annotated[tuple[FinancialMetric, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def statement_semantics(self) -> Self:
        if (self.disclosure_date is None) != (self.disclosure_precision == "unknown"):
            raise ValueError("disclosure date precision must be explicit")
        if (self.period_semantics == "unknown") != (self.period_semantics_evidence is None):
            raise ValueError("period semantics require evidence")
        if len({metric.field_id for metric in self.metrics}) != len(self.metrics):
            raise ValueError("duplicate financial field")
        return self


class MembershipInterval(ResearchRecord):
    kind: Literal["membership_interval"] = "membership_interval"
    universe_id: Text
    valid_from: date
    valid_until: date | None = None
    interval_evidence: Text

    @model_validator(mode="after")
    def half_open_interval(self) -> Self:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("membership interval must be nonempty and half-open")
        return self


class SecurityIdentityChange(ResearchRecord):
    kind: Literal["security_identity_change"] = "security_identity_change"
    security_id: Text
    previous_instrument_id: Text | None = None
    change: Literal["listing", "delisting", "code", "name", "venue"]
    effective_date: date
    previous_name: Text | None = None
    name: Text
    change_evidence: Text

    @model_validator(mode="after")
    def previous_identity(self) -> Self:
        if self.previous_instrument_id is not None:
            check_instrument(self.previous_instrument_id)
        if self.change in {"code", "venue"} and self.previous_instrument_id is None:
            raise ValueError("code/venue change requires previous identity")
        return self


class CorporateAction(ResearchRecord):
    kind: Literal["corporate_action"] = "corporate_action"
    source_event_id: Text
    action: Literal["distribution", "split", "consolidation", "rights"]
    announcement_date: date | None = None
    record_date: date | None = None
    ex_date: date | None = None
    payment_date: date | None = None
    listing_date: date | None = None
    status: Literal["proposed", "approved", "implemented", "cancelled", "unknown"]
    cash: NonNegative | None = None
    cash_currency: Text | None = None
    cash_tax_basis: Literal["before_tax", "after_tax", "unknown"] = "unknown"
    basis_shares: Positive
    bonus_shares: NonNegative | None = None
    transfer_shares: NonNegative | None = None
    split_ratio: Positive | None = None
    rights_shares: NonNegative | None = None
    rights_price: NonNegative | None = None
    terms_evidence: Text

    @model_validator(mode="after")
    def event_terms(self) -> Self:
        if (self.cash is None) != (self.cash_currency is None):
            raise ValueError("cash amount and currency must be specified together")
        if self.action == "distribution":
            if all(v is None for v in (self.cash, self.bonus_shares, self.transfer_shares)):
                raise ValueError("distribution requires terms")
            if any(
                v is not None for v in (self.split_ratio, self.rights_shares, self.rights_price)
            ):
                raise ValueError("distribution cannot carry split/rights terms")
        elif self.action in {"split", "consolidation"}:
            if self.split_ratio is None or self.split_ratio == 1:
                raise ValueError("split/consolidation requires a nonidentity ratio")
            if (self.action == "split") != (self.split_ratio > 1):
                raise ValueError("ratio direction disagrees with action")
            if any(
                v is not None
                for v in (
                    self.cash,
                    self.bonus_shares,
                    self.transfer_shares,
                    self.rights_shares,
                    self.rights_price,
                )
            ):
                raise ValueError("split cannot carry distribution/rights terms")
        elif self.rights_shares is None or self.rights_price is None:
            raise ValueError("rights requires share and subscription price terms")
        elif any(
            v is not None
            for v in (self.cash, self.bonus_shares, self.transfer_shares, self.split_ratio)
        ):
            raise ValueError("rights cannot carry distribution/split terms")
        return self

    @property
    def event_identity(self) -> str:
        return digest([self.scope_id.hex, self.source, self.instrument_id, self.source_event_id])


class AdjustmentFactor(ResearchRecord):
    kind: Literal["adjustment_factor"] = "adjustment_factor"
    observation_date: date
    adjustment: Literal["forward", "backward", "total_return"]
    factor: Positive
    base_date: date
    base_value: Positive
    algorithm_version: Text
    convention_evidence: Text


ResearchObservation = Annotated[
    FinancialStatement
    | MembershipInterval
    | SecurityIdentityChange
    | CorporateAction
    | AdjustmentFactor,
    Field(discriminator="kind"),
]
