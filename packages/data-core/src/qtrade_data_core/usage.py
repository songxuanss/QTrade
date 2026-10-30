"""Admission credits are operator safeguards, never a supplier billing assertion."""

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from qtrade_data_core.ports import DataFailure

DenialReason = Literal["source_daily", "scope_daily", "source_concurrency", "scope_concurrency"]


class UsageDenied(DataFailure):
    def __init__(self, reason: DenialReason, retry_at: datetime | None = None):
        if reason not in {"source_daily", "scope_daily", "source_concurrency", "scope_concurrency"}:
            raise ValueError("invalid budget denial")
        if retry_at is not None and retry_at.utcoffset() is None:
            raise ValueError("explicit budget retry clock required")
        self.reason, self.retry_at = reason, retry_at
        super().__init__("rate_limited")

    def scheduled(self, policy: "UsagePolicy", now: datetime, credits: int = 1) -> "UsageDenied":
        """Daily credit waits for the policy midnight; slots have a short retry.

        This is admission waiting, not a failed supplier request. Do not consume
        the transport failure retry allowance while no request can be admitted.
        """
        if now.utcoffset() is None:
            raise ValueError("explicit budget retry clock required")
        if credits > policy.source_daily or credits > policy.scope_daily:
            # Waiting for midnight cannot make an oversized request admissible.
            # Require a configuration change and explicit retry instead.
            return UsageDenied(self.reason)
        retry = (
            datetime.combine(
                now.astimezone(ZoneInfo(policy.timezone)).date() + timedelta(days=1),
                time(),
                ZoneInfo(policy.timezone),
            )
            if self.reason in {"source_daily", "scope_daily"}
            else now + timedelta(seconds=30)
        )
        return UsageDenied(self.reason, retry)


@dataclass(frozen=True)
class UsagePolicy:
    source_daily: int
    scope_daily: int
    source_concurrency: int
    scope_concurrency: int
    lease_seconds: int = 120
    timezone: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        for value in (self.source_daily, self.scope_daily):
            if type(value) is not int or not 1 <= value <= 1_000_000_000:
                raise ValueError("invalid daily credit safeguard")
        for value in (self.source_concurrency, self.scope_concurrency):
            if type(value) is not int or not 1 <= value <= 1000:
                raise ValueError("invalid concurrency safeguard")
        if type(self.lease_seconds) is not int or not 1 <= self.lease_seconds <= 3600:
            raise ValueError("invalid usage lease")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, TypeError):
            raise ValueError("invalid usage day timezone") from None

    def payload(self) -> dict[str, object]:
        return {"version": "usage-policy.v1", **asdict(self)}


@dataclass(frozen=True)
class UsageRequest:
    request_id: UUID
    source: str
    scope_id: UUID | None
    operation: str
    credits: int

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, UUID) or (
            self.scope_id is not None and not isinstance(self.scope_id, UUID)
        ):
            raise ValueError("invalid usage identity")
        for value in (self.source, self.operation):
            if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9._-]{0,99}", value):
                raise ValueError("usage labels must be safe identifiers")
        if self.scope_id is None and not self.operation.startswith("metadata."):
            raise ValueError("scoped operation needs a workspace identity")
        if type(self.credits) is not int or not 1 <= self.credits <= 1_000_000:
            raise ValueError("invalid request credits")


@dataclass(frozen=True)
class UsageLease:
    request: UsageRequest
    token: UUID
    day: date
    lease_until: datetime
    newly_reserved: bool
    state: Literal["reserved", "finished", "expired"]


@dataclass(frozen=True)
class UsageResult:
    outcome: Literal["success", "failure"]
    duration_ms: int
    response_bytes: int
    error_code: (
        Literal["timeout", "unavailable", "unauthorized", "rate_limited", "unverified"] | None
    ) = None

    def __post_init__(self) -> None:
        if self.outcome not in {"success", "failure"} or any(
            type(value) is not int or not 0 <= value <= 1_000_000_000_000
            for value in (self.duration_ms, self.response_bytes)
        ):
            raise ValueError("invalid usage outcome metrics")
        if self.error_code not in {
            None,
            "timeout",
            "unavailable",
            "unauthorized",
            "rate_limited",
            "unverified",
        }:
            raise ValueError("invalid safe usage error")
        if (self.outcome == "success") != (self.error_code is None):
            raise ValueError("usage outcome and error mismatch")


class UsageLedger(Protocol):
    def reserve(self, request: UsageRequest, policy: UsagePolicy, now: datetime) -> UsageLease: ...
    def finish(self, lease: UsageLease, result: UsageResult, now: datetime) -> None: ...
