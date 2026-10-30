"""Serialize live refresh commits while retaining verified local/publication progress."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from qtrade_data_contracts.freshness import FreshnessCheckpoint, FreshnessKey, FreshnessResult
from qtrade_data_core.freshness import evaluate
from qtrade_data_core.freshness_store import FreshnessRepository, require_snapshot_key
from qtrade_data_core.ports import DataAuthorizer, DataFailure


class FreshnessTransaction(FreshnessRepository, Protocol):
    def notify(self, actor: UUID, result: FreshnessResult) -> None:
        """Record an incident transition in this transaction; never send network I/O."""
        ...


class AtomicFreshnessRepository(FreshnessRepository, Protocol):
    def atomic(self, key: FreshnessKey) -> AbstractContextManager[FreshnessTransaction]: ...


@dataclass(frozen=True)
class RefreshCommit:
    """Superseded requires a newly prepared check; it does not acknowledge storage."""

    state: Literal["recorded", "replayed", "superseded"]
    result: FreshnessResult


def furthest(
    first: FreshnessCheckpoint | None, second: FreshnessCheckpoint | None
) -> FreshnessCheckpoint | None:
    values = [value for value in (first, second) if value is not None]
    return (
        max(values, key=lambda value: (value.complete_through, value.observed_at))
        if values
        else None
    )


class FreshnessRefresh:
    def __init__(self, auth: DataAuthorizer, repository: AtomicFreshnessRepository):
        self.auth, self.repository = auth, repository

    def commit(self, actor: UUID, candidate: FreshnessResult) -> RefreshCommit:
        """Commit a trusted producer's verified candidate, never browser assertions."""
        self.auth.require(actor, candidate.scope_id, "manage")
        try:
            candidate = FreshnessResult.model_validate_json(candidate.model_dump_json())
        except ValueError:
            raise DataFailure("invalid_input") from None
        # Supplier requests, archive verification and policy calculation happen
        # before this short critical section. Only the commit is serialized.
        with self.repository.atomic(candidate.key) as transaction:
            self.auth.require(actor, candidate.scope_id, "manage")
            try:
                previous = transaction.latest(candidate.key)
            except DataFailure as error:
                if error.code != "not_found":
                    raise
                previous = None
            if previous is not None:
                require_snapshot_key(candidate.key, previous)
                if candidate.checked_at < previous.checked_at:
                    # The caller must prepare a new check, not relabel old
                    # supplier observations with a newer wall clock.
                    return RefreshCommit("superseded", previous)
                if (
                    candidate.expectation is not None
                    and previous.expectation is not None
                    and candidate.expectation.rule_version == previous.expectation.rule_version
                    and candidate.expectation.complete_through
                    < previous.expectation.complete_through
                ):
                    # A stale calendar/policy cannot move the target backwards
                    # and thereby make old data look current. An intentional
                    # rule revision needs its own explicit policy version.
                    raise DataFailure("unverified")
                evidence = candidate.evidence.model_copy(
                    update={
                        "collected": furthest(
                            candidate.evidence.collected, previous.evidence.collected
                        ),
                        "published": furthest(
                            candidate.evidence.published, previous.evidence.published
                        ),
                    }
                )
                # Source availability/observations and release rules can regress
                # or become unknown. Never borrow yesterday's source health to
                # hide today's outage or unsupported update policy.
                merged = evaluate(
                    candidate.key, candidate.expectation, evidence, candidate.checked_at
                )
                if candidate.checked_at == previous.checked_at:
                    if merged != previous:
                        raise DataFailure("conflict")
                    return RefreshCommit("replayed", previous)
            else:
                merged = candidate
            stored = transaction.append(merged)
            if stored != merged:
                raise DataFailure("unavailable")
            # A notification failure must roll back the snapshot too. Archival
            # append/record do not emit operational incidents; only this current,
            # authorized refresh path can advance the incident state machine.
            transaction.notify(actor, stored)
            return RefreshCommit("recorded", stored)
