"""Research normalization rejection; no invented standard quantity or PIT claims."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime

from .models import Digest, Text
from .research import ResearchV2
from .research_transport import ResearchKind


class ResearchProcessingFailure(ResearchV2):
    failure_id: UUID
    scope_id: UUID
    request_id: UUID
    batch_id: UUID
    plan_sha256: Digest
    raw_sha256: Digest
    raw_content_sha256: Digest
    source: Text
    kind: ResearchKind
    mapping_version: Text
    checker_version: Literal["research-normalization-check.v1"]
    stage: Literal["normalization"]
    error_code: Literal["unverified"]
    status: Literal["blocked"]
    normalized_record_count: None = None
    failed_at: AwareDatetime
