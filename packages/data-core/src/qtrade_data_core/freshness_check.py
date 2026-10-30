"""One bounded check shared by user actions and future durable refresh workers."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from qtrade_data_contracts.freshness import FreshnessEvidence, FreshnessKey
from qtrade_data_core.default_range import DefaultRange
from qtrade_data_core.freshness import daily_expectation, evaluate
from qtrade_data_core.freshness_evidence import DailyPublicationEvidence, PublicationRepository
from qtrade_data_core.freshness_refresh import FreshnessRefresh, RefreshCommit
from qtrade_data_core.ports import Action, DataAuthorizer, DataFailure
from qtrade_data_core.usage_execution import usage_scope


class DailyExpectationProvider(Protocol):
    def default_range(self, source: str, granularity: str, now: datetime) -> DefaultRange: ...


class FreshnessCheck:
    def __init__(
        self,
        auth: DataAuthorizer,
        publications: PublicationRepository,
        verifier: DailyPublicationEvidence,
        provider: DailyExpectationProvider,
        refresh: FreshnessRefresh,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.auth, self.publications, self.verifier = auth, publications, verifier
        self.provider, self.refresh, self.clock = provider, refresh, clock

    def selection(
        self, actor: UUID, scope: UUID, publication_id: UUID, *, action: Action = "manage"
    ) -> FreshnessKey:
        """Derive a supported daily identity; a client cannot select arbitrary scope/key."""
        self.auth.require(actor, scope, action)
        publication = self.publications.publication(scope, publication_id)
        if publication.scope_id != scope or publication.publication_id != publication_id:
            raise DataFailure("unavailable")
        if (
            publication.dataset_kind != "bars"
            or publication.granularity != "1d"
            or publication.evidence.get("adjustment") != "none"
        ):
            raise DataFailure("unsupported")
        return FreshnessKey(
            scope_id=scope,
            source=publication.source,
            instrument_id=publication.instrument_id,
            dataset_kind="bars",
            period="1d",
            adjustment="unadjusted",
            date_axis="observation_date",
            mapping_version=publication.mapping_version,
        )

    def latest_publication(self, actor: UUID, key: FreshnessKey) -> UUID:
        self.auth.require(actor, key.scope_id, "manage")
        publication = self.publications.latest_publication(key)
        if self.selection(actor, key.scope_id, publication.publication_id) != key:
            raise DataFailure("unverified")
        return publication.publication_id

    def check(
        self,
        actor: UUID,
        scope: UUID,
        publication_id: UUID,
        *,
        expected_key: FreshnessKey | None = None,
    ) -> RefreshCommit:
        """Verify saved price evidence against today's rule and store a merged check.

        This command may load calendar metadata under the caller's budget. It
        never downloads newer prices; unsupported or unverifiable evidence is
        an explicit failure, not a synthetic healthy snapshot.
        """
        key = self.selection(actor, scope, publication_id)
        if expected_key is not None and key != expected_key:
            raise DataFailure("unverified")
        # Retry only after preparing policy/evidence again. Never advance an old
        # candidate's clock to defeat superseded or a release-boundary change.
        for _ in range(2):
            self.auth.require(actor, scope, "manage")
            with usage_scope(actor, scope):
                expectation = daily_expectation(
                    key, self.provider.default_range(key.source, "1d", self.clock())
                )
            proof = self.verifier.verify(actor, scope, publication_id, expectation=expectation)
            if proof.key != key:
                raise DataFailure("unavailable")
            checked_at = self.clock()
            with usage_scope(actor, scope):
                current = daily_expectation(
                    key, self.provider.default_range(key.source, "1d", checked_at)
                )
            if current != expectation:
                continue
            # A recent verified price acquisition that includes the expected
            # period is a source observation. Old historical requests cannot
            # establish supplier freshness or transport health today.
            age = (checked_at - proof.collected.observed_at).total_seconds()
            source = (
                proof.collected
                if 0 <= age <= 300 and proof.collected.complete_through >= current.complete_through
                else None
            )
            candidate = evaluate(
                key,
                current,
                FreshnessEvidence(
                    key=key,
                    source_health="available" if source else "unknown",
                    source=source,
                    collected=proof.collected,
                    published=proof.published,
                ),
                checked_at,
            )
            outcome = self.refresh.commit(actor, candidate)
            if outcome.state != "superseded":
                return outcome
        raise DataFailure("conflict")
