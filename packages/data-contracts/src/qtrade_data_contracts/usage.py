"""Scoped usage evidence: operator credits, never supplier billing or rights."""

from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .models import DataContract

Count = Annotated[int, Field(ge=0)]
Label = Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{0,99}$")]
DenialReason = Literal["source_daily", "scope_daily", "source_concurrency", "scope_concurrency"]


class UsageBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    daily_limit: int = Field(gt=0)
    credits_used: Count
    active_reservations: Count
    concurrency_limit: int = Field(gt=0)


class UsageRequestView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    request_id: UUID
    operation: Label
    credits: int = Field(gt=0)
    reserved_at: AwareDatetime
    lease_until: AwareDatetime
    state: Literal["reserved", "finished", "expired"]
    lease_elapsed: bool
    outcome: Literal["success", "failure", "expired"] | None
    error_code: (
        Literal["timeout", "unavailable", "unauthorized", "rate_limited", "unverified"] | None
    )
    duration_ms: Count | None
    response_bytes: Count | None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.lease_until <= self.reserved_at:
            raise ValueError("invalid usage ownership interval")
        if self.state == "finished":
            if self.outcome not in {"success", "failure"} or (
                self.duration_ms is None or self.response_bytes is None
            ):
                raise ValueError("completed usage requires metrics")
            if (self.outcome == "success") != (self.error_code is None):
                raise ValueError("usage completion error mismatch")
        elif self.state == "reserved" and any(
            v is not None
            for v in (self.outcome, self.error_code, self.duration_ms, self.response_bytes)
        ):
            raise ValueError("unfinished ownership cannot claim a result")
        elif self.state == "expired" and self.outcome != "expired":
            raise ValueError("expired ownership requires explicit outcome")
        return self


class UsageDenialView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    request_id: UUID
    operation: Label
    credits: int = Field(gt=0)
    reason: DenialReason
    denied_at: AwareDatetime


class UsageSnapshot(DataContract):
    source: Label
    day: date
    timezone: str
    generated_at: AwareDatetime
    resets_at: AwareDatetime
    source_budget: UsageBudget
    scope_budget: UsageBudget
    request_count: Count
    success_count: Count
    failure_count: Count
    expired_count: Count
    denial_count: Count
    response_bytes: Count
    recent_requests: tuple[UsageRequestView, ...] = Field(max_length=50)
    recent_denials: tuple[UsageDenialView, ...] = Field(max_length=50)
    accounting: Literal["internal_credit_safeguard"]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        try:
            zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown budget day timezone") from None
        if (
            self.generated_at.astimezone(zone).date() != self.day
            or self.resets_at <= self.generated_at
        ):
            raise ValueError("snapshot budget day mismatch")
        if self.source_budget.credits_used < self.scope_budget.credits_used or (
            self.source_budget.active_reservations < self.scope_budget.active_reservations
        ):
            raise ValueError("scope cannot exceed shared source usage")
        if self.success_count + self.failure_count + self.expired_count > self.request_count:
            raise ValueError("usage outcomes exceed total requests")
        for rows, total in (
            (self.recent_requests, self.request_count),
            (self.recent_denials, self.denial_count),
        ):
            if len(rows) > total or len({r.request_id for r in rows}) != len(rows):
                raise ValueError("usage details exceed totals or repeat identity")
        if any(
            r.lease_elapsed != (r.lease_until <= self.generated_at) for r in self.recent_requests
        ):
            raise ValueError("ownership evidence contradicts snapshot clock")
        return self
