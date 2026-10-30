"""Explicit watch controls and one recoverable tick; no implicit price acquisition."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID, uuid5

from qtrade_data_contracts.collection import digest, instrument_key
from qtrade_data_contracts.freshness import FreshnessKey
from qtrade_data_contracts.freshness_watch import (
    FreshnessWatch,
    FreshnessWatchControl,
    FreshnessWatchCreate,
)
from qtrade_data_contracts.models import ErrorCode
from qtrade_data_core.freshness_check import FreshnessCheck
from qtrade_data_core.ports import DataAuthorizer, DataFailure


@dataclass(frozen=True)
class FreshnessWatchLease:
    watch: FreshnessWatch
    token: UUID
    expires_at: datetime


class FreshnessWatchRepository(Protocol):
    def create(
        self, actor: UUID, publication: UUID, key: FreshnessKey, interval: int
    ) -> FreshnessWatch: ...
    def get(self, scope: UUID, watch: UUID) -> FreshnessWatch: ...
    def control(
        self, actor: UUID, scope: UUID, watch: UUID, body: FreshnessWatchControl
    ) -> FreshnessWatch: ...
    def claim(self) -> FreshnessWatchLease | None: ...
    def finish(
        self,
        lease: FreshnessWatchLease,
        state: Literal["completed", "deferred", "paused"],
        error: ErrorCode | None,
        checked_at: datetime | None,
    ) -> bool: ...


class FreshnessWatches:
    def __init__(
        self, auth: DataAuthorizer, repository: FreshnessWatchRepository, checker: FreshnessCheck
    ):
        self.auth, self.repository, self.checker = auth, repository, checker

    def create(self, actor: UUID, scope: UUID, body: FreshnessWatchCreate) -> FreshnessWatch:
        self.auth.require(actor, scope, "manage")
        key = self.checker.selection(actor, scope, body.publication_id)
        if instrument_key(key.instrument_id)[1] != "CN":
            raise DataFailure("unsupported")
        return self.repository.create(actor, body.publication_id, key, body.interval_seconds)

    def by_publication(self, actor: UUID, scope: UUID, publication: UUID) -> FreshnessWatch:
        key = self.checker.selection(actor, scope, publication, action="read")
        value = self.get(
            actor, scope, uuid5(scope, "freshness-watch:" + digest(key.model_dump(mode="json")))
        )
        if value.key != key:
            raise DataFailure("unverified")
        return value

    def get(self, actor: UUID, scope: UUID, watch: UUID) -> FreshnessWatch:
        self.auth.require(actor, scope, "read")
        value = self.repository.get(scope, watch)
        try:
            value = FreshnessWatch.model_validate_json(value.model_dump_json())
        except ValueError:
            raise DataFailure("unverified") from None
        if value.scope_id != scope or value.watch_id != watch:
            raise DataFailure("unverified")
        return value

    def control(
        self, actor: UUID, scope: UUID, watch: UUID, body: FreshnessWatchControl
    ) -> FreshnessWatch:
        self.auth.require(actor, scope, "manage")
        value = self.get(actor, scope, watch)
        if body.enabled:
            # Enabling explicitly adopts the current actor as execution subject.
            # Do not revive a revoked prior owner behind the user's back.
            key = self.checker.selection(actor, scope, value.reference_publication_id)
            if key != value.key:
                raise DataFailure("unverified")
        changed = self.repository.control(actor, scope, watch, body)
        if (
            changed.scope_id != scope
            or changed.watch_id != watch
            or changed.key != value.key
            or changed.enabled != body.enabled
            or changed.revision != body.revision + 1
            or (body.enabled and changed.actor_id != actor)
        ):
            raise DataFailure("unverified")
        return changed


class FreshnessWatchRunner:
    def __init__(
        self,
        auth: DataAuthorizer,
        repository: FreshnessWatchRepository,
        checker: Callable[[FreshnessWatchLease], FreshnessCheck],
    ):
        self.auth, self.repository, self.checker = auth, repository, checker

    def run_once(self) -> FreshnessWatchLease | None:
        lease = self.repository.claim()
        if lease is None:
            return None
        watch = lease.watch
        try:
            self.auth.require(watch.actor_id, watch.scope_id, "manage")
            checker = self.checker(lease)
            publication = checker.latest_publication(watch.actor_id, watch.key)
            outcome = checker.check(
                watch.actor_id, watch.scope_id, publication, expected_key=watch.key
            )
            self.repository.finish(lease, "completed", None, outcome.result.checked_at)
        except DataFailure as error:
            state: Literal["paused", "deferred"] = (
                "paused"
                if error.code in {"forbidden", "unauthorized", "unsupported"}
                else "deferred"
            )
            self.repository.finish(lease, state, error.code, None)
        except Exception:
            # Fail closed with a safe diagnostic. Keep the persisted identity
            # and back off; an exception does not certify fresh/healthy data.
            self.repository.finish(lease, "deferred", "unavailable", None)
        return lease
