"""Evidence storage seams used before quality evaluation or consumer publication."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrade_data_contracts.research import ResearchObservation
from qtrade_data_contracts.research_archive import ResearchArtifactManifest, ResearchRawManifest
from qtrade_data_contracts.research_processing import ResearchProcessingFailure
from qtrade_data_contracts.research_transport import ResearchPartition, ResearchPlan
from qtrade_data_core.research import ResearchRawPage


class ResearchArchivePort(Protocol):
    def record_processing_failure(
        self, plan: ResearchPlan, raw: ResearchRawManifest, failure: ResearchProcessingFailure
    ) -> None: ...
    def processing_failures(self, plan: ResearchPlan) -> tuple[ResearchProcessingFailure, ...]: ...
    def capture(
        self,
        plan: ResearchPlan,
        partition: ResearchPartition,
        page: ResearchRawPage,
        collected_at: datetime,
    ) -> ResearchRawManifest: ...
    def raw(self, manifest: ResearchRawManifest) -> tuple[dict[str, object], ...]: ...
    def standardize(
        self,
        plan: ResearchPlan,
        batches: tuple[ResearchRawManifest, ...],
        records: tuple[ResearchObservation, ...],
        created_at: datetime,
    ) -> ResearchArtifactManifest: ...
    def records(self, manifest: ResearchArtifactManifest) -> tuple[ResearchObservation, ...]: ...


class ResearchEvidenceRepository(Protocol):
    def register(self, plan: ResearchPlan) -> None: ...
    def append_raw(self, manifest: ResearchRawManifest) -> None: ...
    def append_artifact(self, manifest: ResearchArtifactManifest) -> None: ...
    def raw_manifest(self, scope: UUID, batch_id: UUID) -> ResearchRawManifest: ...
    def artifact_manifest(self, scope: UUID, artifact_id: UUID) -> ResearchArtifactManifest: ...
    def stage(self, scope: UUID, request_id: UUID) -> str: ...
