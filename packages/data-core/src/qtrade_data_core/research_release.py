"""Quality-bound research release orchestration with current authorization."""

import json
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid5

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.research_archive import ResearchArtifactManifest
from qtrade_data_contracts.research_publication import (
    ResearchPublicationManifest,
    ResearchQualityResult,
)
from qtrade_data_contracts.research_transport import ResearchPlan
from qtrade_data_core.ports import DataAuthorizer, DataFailure
from qtrade_data_core.research_evidence import ResearchArchivePort
from qtrade_data_core.research_quality import evaluate


class ResearchReleaseRepository(Protocol):
    def artifact_manifest(self, scope: UUID, artifact_id: UUID) -> ResearchArtifactManifest: ...
    def plan(self, scope: UUID, request_id: UUID) -> ResearchPlan: ...
    def record_quality(self, result: ResearchQualityResult) -> ResearchQualityResult: ...
    def publish(self, manifest: ResearchPublicationManifest) -> ResearchPublicationManifest: ...


class ResearchReleaseService:
    def __init__(
        self,
        auth: DataAuthorizer,
        archive: ResearchArchivePort,
        repository: ResearchReleaseRepository,
    ):
        self.auth, self.archive, self.repository = auth, archive, repository

    def review(self, actor: UUID, scope: UUID, artifact_id: UUID) -> ResearchQualityResult:
        self.auth.require(actor, scope, "manage")
        artifact = self.repository.artifact_manifest(scope, artifact_id)
        plan = self.repository.plan(scope, artifact.request_id)
        result = evaluate(plan, artifact, self.archive, datetime.now(UTC))
        return self.repository.record_quality(result)

    def publish(self, actor: UUID, scope: UUID, artifact_id: UUID) -> ResearchPublicationManifest:
        quality = self.review(actor, scope, artifact_id)
        if quality.status == "blocked":
            raise DataFailure("unverified")
        self.auth.require(actor, scope, "manage")
        artifact = self.repository.artifact_manifest(scope, artifact_id)
        # Recheck bytes immediately before creating a reference, including on retries.
        self.archive.records(artifact)
        publication_id = uuid5(artifact.artifact_id, str(quality.quality_id))
        body = dict(
            schema_version=2,
            scope_id=str(scope),
            publication_id=str(publication_id),
            artifact=artifact.model_dump(mode="json"),
            quality=quality.model_dump(mode="json"),
            published_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        manifest = ResearchPublicationManifest.model_validate_json(
            json.dumps(body | {"content_version": digest(body)})
        )
        return self.repository.publish(manifest)
