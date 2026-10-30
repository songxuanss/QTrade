"""Versioned quality decisions and immutable publication references."""

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, model_validator

from .collection import digest
from .models import Digest, Text
from .research import ResearchV2
from .research_archive import ResearchArtifactManifest


class ResearchQualityIssue(ResearchV2):
    code: Text
    severity: Literal["block", "warning"]
    reason: Text
    record_version: Digest | None = None


class ResearchQualityResult(ResearchV2):
    scope_id: UUID
    artifact_id: UUID
    quality_id: UUID
    rule_version: Text
    records_sha256: Digest
    status: Literal["passed", "warning", "blocked"]
    issues: tuple[ResearchQualityIssue, ...]
    checked_at: AwareDatetime

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        expected = (
            "blocked"
            if any(i.severity == "block" for i in self.issues)
            else "warning"
            if self.issues
            else "passed"
        )
        if self.status != expected:
            raise ValueError("quality status disagrees with issues")
        return self


class ResearchPublicationManifest(ResearchV2):
    scope_id: UUID
    publication_id: UUID
    artifact: ResearchArtifactManifest
    quality: ResearchQualityResult
    published_at: AwareDatetime
    content_version: Digest

    @model_validator(mode="after")
    def accepted_publication(self) -> Self:
        if (
            self.scope_id != self.artifact.scope_id
            or self.scope_id != self.quality.scope_id
            or self.artifact.artifact_id != self.quality.artifact_id
            or self.artifact.records_sha256 != self.quality.records_sha256
        ):
            raise ValueError("quality decision does not bind this artifact")
        if self.quality.status == "blocked":
            raise ValueError("blocked artifact cannot be published")
        if self.content_version != digest(
            self.model_dump(mode="json", exclude={"content_version"})
        ):
            raise ValueError("publication checksum mismatch")
        return self
