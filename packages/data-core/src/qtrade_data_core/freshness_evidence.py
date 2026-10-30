"""Reproducible publication evidence; historical downloads are not latest-source probes."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from qtrade_data_contracts.collection import (
    CollectedBar,
    CollectionPublication,
    CollectionRawCapture,
    FactorPoint,
    digest,
    instrument_key,
)
from qtrade_data_contracts.collection_quality import CollectionQualityResult
from qtrade_data_contracts.freshness import (
    FreshnessCheckpoint,
    FreshnessExpectation,
    FreshnessKey,
)
from qtrade_data_core.collection import CollectionPart, CollectionResult
from qtrade_data_core.collection_quality import evaluate
from qtrade_data_core.ports import DataAuthorizer, DataFailure


class PublicationRepository(Protocol):
    def latest_publication(self, key: FreshnessKey) -> CollectionPublication: ...
    def publication(self, scope_id: UUID, publication_id: UUID) -> CollectionPublication: ...
    def part_for_job(self, scope_id: UUID, job_id: UUID, part_id: UUID) -> CollectionPart: ...


class PublicationEvidenceArchive(Protocol):
    def recover(self, part: CollectionPart) -> CollectionPublication | None: ...
    def raw_records(self, publication: CollectionPublication) -> tuple[dict[str, object], ...]: ...
    def records(
        self, publication: CollectionPublication
    ) -> tuple[CollectedBar | FactorPoint, ...]: ...
    def qualities(self, part: CollectionPart) -> tuple[CollectionQualityResult, ...]: ...
    def source_row_proofs(
        self, part: CollectionPart, before: datetime
    ) -> tuple[tuple[CollectionRawCapture, frozenset[str]], ...]: ...


class ReplayAcquisitionEvidence(Protocol):
    def proofs(
        self, actor: UUID, part: CollectionPart, publication: CollectionPublication
    ) -> tuple[tuple[CollectionRawCapture, frozenset[str]], ...]: ...


@dataclass(frozen=True)
class VerifiedDailyPublication:
    key: FreshnessKey
    publication_id: UUID
    collected: FreshnessCheckpoint
    published: FreshnessCheckpoint


class DailyPublicationEvidence:
    def __init__(
        self,
        auth: DataAuthorizer,
        repository: PublicationRepository,
        archive: PublicationEvidenceArchive,
        *,
        replays: ReplayAcquisitionEvidence | None = None,
    ):
        self.auth, self.repository, self.archive = auth, repository, archive
        self.replays = replays

    def verify(
        self,
        actor: UUID,
        scope: UUID,
        publication_id: UUID,
        *,
        expectation: FreshnessExpectation | None = None,
    ) -> VerifiedDailyPublication:
        self.auth.require(actor, scope, "read")
        publication = self.repository.publication(scope, publication_id)
        if publication.scope_id != scope or publication.publication_id != publication_id:
            raise DataFailure("unavailable")
        if instrument_key(publication.instrument_id)[1] != "CN":
            # Other markets need an explicit calendar/time-label policy, rather
            # than inheriting the first A-share adapter's market timezone.
            raise DataFailure("unsupported")
        if (
            publication.dataset_kind != "bars"
            or publication.granularity != "1d"
            or publication.source_time_convention != "local_trade_date"
            or publication.evidence.get("adjustment") != "none"
            or publication.record_count > 20000
            or publication.raw_record_count > 20000
        ):
            raise DataFailure("unsupported")
        part = self.repository.part_for_job(scope, publication.job_id, publication.part_id)
        if (
            part.request.scope_id != scope
            or part.state != "completed"
            or part.part_id != publication.part_id
            or part.job_id != publication.job_id
            or part.request.actor_id != publication.actor_id
            or part.instrument_id != publication.instrument_id
            or part.request.source != publication.source
            or part.mapping_version != publication.mapping_version
        ):
            raise DataFailure("unavailable")
        if self.archive.recover(part) != publication:
            raise DataFailure("unverified")
        records = self.archive.records(publication)
        raw = self.archive.raw_records(publication)
        if digest([row.model_dump(mode="json") for row in records]) != publication.records_sha256:
            raise DataFailure("unverified")
        result = CollectionResult(
            raw,
            records,
            publication.units,
            publication.source_time_convention,
            publication.evidence,
        )
        checks = [
            q
            for q in self.archive.qualities(part)
            if q.records_sha256 == publication.records_sha256
            and q.status in {"passed", "warning"}
            and q.checked_at <= publication.published_at
        ]
        # Replaying the actual raw/standard inputs binds units, dates, source
        # evidence and mapping. A matching row hash alone is not quality proof.
        quality = next(
            (
                q
                for q in checks
                if evaluate(part, result, "Asia/Shanghai", q.checked_at, source_captured=True) == q
            ),
            None,
        )
        if quality is None:
            raise DataFailure("unverified")
        if (
            part.execution_kind == "normalization_replay"
            or "normalization_replay" in publication.evidence
        ):
            if part.execution_kind != "normalization_replay" or self.replays is None:
                raise DataFailure("unverified")
            # Follow immutable root acquisition, never the new processing clock.
            source_proofs = self.replays.proofs(actor, part, publication)
        else:
            source_proofs = self.archive.source_row_proofs(part, quality.checked_at)
        row_proofs = [
            (capture, rows) for capture, rows in source_proofs if capture.kind == "history" and rows
        ]
        raw_hashes = {digest(row) for row in raw}
        if not row_proofs or not raw_hashes:
            raise DataFailure("unverified")
        zone = ZoneInfo("Asia/Shanghai")
        # Only a complete, calendar-backed daily period is a checkpoint. A
        # partial-day request or suspect candle cannot certify that day's data.
        if expectation is not None and expectation.timezone != "Asia/Shanghai":
            raise DataFailure("unsupported")
        days = []
        expected = publication.evidence.get("expected_dates")
        if not isinstance(expected, list):
            raise DataFailure("unverified")
        for row in records:
            if (
                not isinstance(row, CollectedBar)
                or row.instrument_id != publication.instrument_id
                or row.granularity != "1d"
            ):
                raise DataFailure("unverified")
            local = row.timestamp.astimezone(zone)
            end = datetime.combine(local.date() + timedelta(days=1), time.min, zone)
            if (
                local.time() == time.min
                and local.date() <= publication.published_at.astimezone(zone).date()
                and row.quality != "suspect"
                and publication.start <= local
                and end <= publication.end
                and local.date().isoformat() in expected
            ):
                days.append(local.date())
        # A receipt reread from cache does not establish original acquisition
        # time. Require exact selected raw rows in real history receipts, after
        # the relevant release boundary. Without a declared rule, only a period
        # acquired on a later local day can be certified conservatively.
        captures = []
        complete_day = None
        for day in sorted(set(days), reverse=True):
            if expectation is not None:
                if day > expectation.complete_through:
                    continue
                # Availability may fall on a later date. Do not reduce it to a
                # wall-clock time or extrapolate one date's rule onto history.
                release = (
                    expectation.available_at
                    if day == expectation.complete_through
                    else datetime.combine(day + timedelta(days=1), time.min, zone)
                )
            else:
                release = datetime.combine(day + timedelta(days=1), time.min, zone)
            eligible = [(c, rows) for c, rows in row_proofs if c.captured_at >= release]
            covered = set().union(*(rows for _, rows in eligible)) if eligible else set()
            if raw_hashes <= covered:
                complete_day = day
                captures = [c for c, rows in eligible if rows & raw_hashes]
                break
        if complete_day is None:
            raise DataFailure("unverified")
        key = FreshnessKey(
            scope_id=scope,
            source=publication.source,
            instrument_id=publication.instrument_id,
            dataset_kind="bars",
            period="1d",
            adjustment="unadjusted",
            date_axis="observation_date",
            mapping_version=publication.mapping_version,
        )
        proof = digest(
            {
                "publication": publication.model_dump(mode="json"),
                "quality": quality.model_dump(mode="json"),
                "captures": [c.model_dump(mode="json") for c in captures],
            }
        )
        return VerifiedDailyPublication(
            key,
            publication_id,
            FreshnessCheckpoint(
                complete_through=complete_day,
                observed_at=max(c.captured_at for c in captures),
                evidence_sha256=proof,
            ),
            FreshnessCheckpoint(
                complete_through=complete_day,
                observed_at=publication.published_at,
                evidence_sha256=proof,
            ),
        )
