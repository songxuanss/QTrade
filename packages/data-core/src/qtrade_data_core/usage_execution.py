"""Trusted execution scope and vendor-neutral request admission bookkeeping."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import monotonic
from types import MappingProxyType
from typing import Literal
from uuid import UUID, uuid4

from qtrade_data_core.ports import DataFailure
from qtrade_data_core.usage import UsageDenied, UsageLedger, UsagePolicy, UsageRequest, UsageResult


@dataclass(frozen=True)
class UsageIdentity:
    actor_id: UUID
    scope_id: UUID

    def __post_init__(self) -> None:
        if not all(isinstance(value, UUID) for value in (self.actor_id, self.scope_id)):
            raise ValueError("trusted usage identity required")


@dataclass(frozen=True)
class ScopeLimits:
    daily_credits: int
    concurrency: int

    def __post_init__(self) -> None:
        UsagePolicy(1, self.daily_credits, 1, self.concurrency)


usage_identity: ContextVar[UsageIdentity | None] = ContextVar("usage_identity", default=None)


@contextmanager
def usage_scope(actor: UUID, scope: UUID) -> Iterator[None]:
    """Enter only after current authorization; ContextVar keeps concurrent scopes separate."""
    token = usage_identity.set(UsageIdentity(actor, scope))
    try:
        yield
    finally:
        usage_identity.reset(token)


class UsageAttempt:
    def __init__(self, ledger: UsageLedger, request: UsageRequest, policy: UsagePolicy):
        self.ledger = ledger
        try:
            self.lease = ledger.reserve(request, policy, datetime.now(UTC))
        except UsageDenied as error:
            raise error.scheduled(policy, datetime.now(UTC), request.credits) from None
        if not self.lease.newly_reserved or self.lease.state != "reserved":
            raise DataFailure("conflict")
        # Transport duration excludes pacing and admission/database contention;
        # the provider records pacing separately for operational attribution.
        self.started = monotonic()

    def transport_budget(self, maximum: float) -> float:
        """Reject stale admission before I/O; retain time for cleanup/finalization.

        Wall-clock ownership and elapsed transport are separate clocks. This
        does not certify behavior under host suspension or arbitrary clock jumps.
        """
        remaining = (self.lease.lease_until - datetime.now(UTC)).total_seconds() - 10
        if remaining <= 0:
            raise DataFailure("timeout")
        return min(maximum, remaining)

    def finish(self, response_bytes: int, error: str | None) -> None:
        safe_error: (
            Literal["timeout", "unavailable", "unauthorized", "rate_limited", "unverified"] | None
        )
        if error == "timeout":
            safe_error = "timeout"
        elif error == "unauthorized":
            safe_error = "unauthorized"
        elif error == "rate_limited":
            safe_error = "rate_limited"
        elif error == "unverified":
            safe_error = "unverified"
        else:
            safe_error = "unavailable" if error else None
        result = UsageResult(
            "failure" if error else "success",
            round((monotonic() - self.started) * 1000),
            response_bytes,
            safe_error,
        )
        self.ledger.finish(self.lease, result, datetime.now(UTC))


class UsageGate:
    """Credits before outbound I/O; callers own transport lifetime and cancellation.

    This gate does not certify physical socket concurrency when ownership expires.
    The transport must finish/terminate or maintain ownership before slot expiry.
    """

    def __init__(
        self,
        ledger: UsageLedger,
        source: str,
        policy: UsagePolicy,
        scope_limits: Mapping[UUID, ScopeLimits] | None = None,
    ):
        self.ledger, self.source, self.policy = ledger, source, policy
        limits = dict(scope_limits or {})
        if any(
            not isinstance(k, UUID) or not isinstance(v, ScopeLimits) for k, v in limits.items()
        ):
            raise ValueError("trusted scoped policy required")
        self._scope_limits = MappingProxyType(limits)

    def policy_for(self, scope: UUID | None) -> UsagePolicy:
        """Operator overrides affect this scope only, never the shared source cap.

        Caller identity is not configuration. Unknown scopes use the default;
        global background metadata cannot borrow any tenant-specific allowance.
        """
        limits = self._scope_limits.get(scope) if scope else None
        return (
            replace(
                self.policy, scope_daily=limits.daily_credits, scope_concurrency=limits.concurrency
            )
            if limits
            else self.policy
        )

    def begin(self, operation: str, credits: int) -> UsageAttempt:
        identity = usage_identity.get()
        try:
            request = UsageRequest(
                uuid4(), self.source, identity.scope_id if identity else None, operation, credits
            )
        except ValueError:
            # Missing collection/research context fails closed before a network call.
            raise DataFailure("unavailable") from None
        return UsageAttempt(self.ledger, request, self.policy_for(request.scope_id))
