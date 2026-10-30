"""Bounded research execution shared by typed source adapters.

This synchronous execution seam does not itself provide job leases or scheduling.
Raw evidence is durable before normalization, including failed/empty responses.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid5

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.research import ResearchObservation
from qtrade_data_contracts.research_archive import ResearchArtifactManifest, ResearchRawManifest
from qtrade_data_contracts.research_processing import ResearchProcessingFailure
from qtrade_data_contracts.research_transport import ResearchPlan
from qtrade_data_core.ports import DataAuthorizer, DataFailure
from qtrade_data_core.research import ResearchProvider, ResearchRawPage
from qtrade_data_core.research_acquisition import (
    CapturedResearchProvider,
    ResearchAcquisitionEvidence,
)
from qtrade_data_core.research_evidence import ResearchArchivePort, ResearchEvidenceRepository
from qtrade_data_core.usage_execution import usage_scope


@dataclass(frozen=True)
class ResearchCollectionResult:
    raw_batches: tuple[ResearchRawManifest, ...]
    artifact: ResearchArtifactManifest | None
    # Empty observations retain their evidence but cannot become publications.
    status: str


def normalize_page(
    archive: ResearchArchivePort,
    plan: ResearchPlan,
    provider: ResearchProvider,
    page: ResearchRawPage,
    raw: ResearchRawManifest,
    clock: Callable[[], datetime],
) -> tuple[ResearchObservation, ...]:
    """One normalization/rejection boundary shared by download and offline replay."""
    request = plan.request
    try:
        normalized = provider.normalize(request, page, raw.collected_at)
        if not page.rows and normalized:
            raise DataFailure("unverified")
    except (DataFailure, ValueError) as error:
        # Typed adapters may reject malformed input through Pydantic
        # (a ValueError). This is the same conversion boundary as an
        # explicit unverified result; never persist exception text.
        code = error.code if isinstance(error, DataFailure) else "unverified"
        if code == "unverified":
            # The durable raw page precedes conversion. Preserve a
            # safe versioned rejection, not the provider exception
            # text or a fabricated zero standard-record count.
            identity = digest(
                {
                    "plan": plan.plan_sha256,
                    "batch": str(raw.batch_id),
                    "raw": raw.raw_sha256,
                    "mapping": plan.capability.mapping_version,
                    "checker": "research-normalization-check.v1",
                }
            )
            failure = ResearchProcessingFailure(
                failure_id=uuid5(request.request_id, identity),
                scope_id=request.scope_id,
                request_id=request.request_id,
                batch_id=raw.batch_id,
                plan_sha256=plan.plan_sha256,
                raw_sha256=raw.raw_sha256,
                raw_content_sha256=raw.raw_content_sha256,
                source=request.source,
                kind=request.kind,
                mapping_version=plan.capability.mapping_version,
                checker_version="research-normalization-check.v1",
                stage="normalization",
                error_code="unverified",
                status="blocked",
                failed_at=clock(),
            )
            try:
                archive.record_processing_failure(plan, raw, failure)
            except Exception:
                raise DataFailure("unavailable") from None
        raise DataFailure(code) from None
    return normalized


class ResearchCollectionExecutor:
    def __init__(
        self,
        auth: DataAuthorizer,
        archive: ResearchArchivePort,
        repository: ResearchEvidenceRepository,
        *,
        max_pages_per_partition: int = 1000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        acquisition: ResearchAcquisitionEvidence | None = None,
    ):
        if type(max_pages_per_partition) is not int or max_pages_per_partition < 1:
            raise ValueError("positive page bound required")
        self.auth, self.archive, self.repository = auth, archive, repository
        self.max_pages, self.clock = max_pages_per_partition, clock
        self.acquisition = acquisition

    def collect(self, plan: ResearchPlan, provider: ResearchProvider) -> ResearchCollectionResult:
        self.auth.require(plan.request.actor_id, plan.request.scope_id, "manage")
        with usage_scope(plan.request.actor_id, plan.request.scope_id):
            return self._collect(plan, provider)

    def _collect(self, plan: ResearchPlan, provider: ResearchProvider) -> ResearchCollectionResult:
        request = plan.request
        self.auth.require(request.actor_id, request.scope_id, "manage")
        if plan.capability not in provider.capabilities() or not plan.capability.configured:
            raise DataFailure("conflict")
        self.repository.register(plan)
        batches: list[ResearchRawManifest] = []
        records: list[ResearchObservation] = []
        for partition in plan.partitions:
            cursor = None
            seen: set[str | None] = set()
            for _ in range(self.max_pages):
                self.auth.require(request.actor_id, request.scope_id, "manage")
                if self.acquisition is not None and isinstance(provider, CapturedResearchProvider):
                    page = self.acquisition.fetch(plan, provider, partition, cursor, self.clock)
                else:
                    page = provider.fetch(request, partition, cursor)
                # A nonadvancing cursor cannot form a valid raw manifest.
                if page.next_cursor is not None and page.next_cursor == page.cursor:
                    raise DataFailure("unverified")
                raw = self.archive.capture(plan, partition, page, self.clock())
                self.repository.append_raw(raw)
                batches.append(raw)
                # Preserve even malformed paging evidence before rejecting it.
                if page.cursor != cursor or cursor in seen:
                    raise DataFailure("unverified")
                seen.add(cursor)
                normalized = normalize_page(self.archive, plan, provider, page, raw, self.clock)
                records.extend(normalized)
                if page.next_cursor is None:
                    break
                if page.next_cursor in seen:
                    raise DataFailure("unverified")
                cursor = page.next_cursor
            else:
                raise DataFailure("unverified")
        self.auth.require(request.actor_id, request.scope_id, "manage")
        if not records:
            return ResearchCollectionResult(tuple(batches), None, "no_observations")
        artifact = self.archive.standardize(plan, tuple(batches), tuple(records), self.clock())
        self.repository.append_artifact(artifact)
        return ResearchCollectionResult(tuple(batches), artifact, "standard_persisted")
