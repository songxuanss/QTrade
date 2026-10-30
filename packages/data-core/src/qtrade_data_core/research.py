"""Research discovery/planning/query seam; vendor and persistence stay in adapters."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrade_data_contracts.collection import digest, instrument_key
from qtrade_data_contracts.planning import CollectionCostEstimate, CollectionPlanGuard
from qtrade_data_contracts.research import ResearchObservation
from qtrade_data_contracts.research_planning import ResearchPlanPreview
from qtrade_data_contracts.research_processing import ResearchProcessingFailure
from qtrade_data_contracts.research_publication import ResearchPublicationManifest
from qtrade_data_contracts.research_transport import (
    ResearchCapability,
    ResearchPartition,
    ResearchPlan,
    ResearchPlanRequest,
    ResearchQuery,
    ResearchRecordPage,
)
from qtrade_data_core.ports import DataAuthorizer, DataFailure


@dataclass(frozen=True)
class ResearchRawPage:
    # Arbitrary fields are confined to supplier RAW evidence, never standard records.
    rows: tuple[dict[str, object], ...]
    cursor: str | None
    next_cursor: str | None


class ResearchProvider(Protocol):
    def estimate(self, request: ResearchPlanRequest, parts: int) -> CollectionCostEstimate:
        """Estimate bounded future fetches, without HTTP or storage mutation."""
        ...

    def capabilities(self) -> tuple[ResearchCapability, ...]: ...
    def partitions(self, request: ResearchPlanRequest) -> Iterator[ResearchPartition]: ...
    def fetch(
        self, request: ResearchPlanRequest, partition: ResearchPartition, cursor: str | None
    ) -> ResearchRawPage: ...
    def normalize(
        self, request: ResearchPlanRequest, page: ResearchRawPage, collected_at: datetime
    ) -> tuple[ResearchObservation, ...]: ...


@dataclass(frozen=True)
class ResearchPublicationReference:
    scope_id: UUID
    publication_id: UUID
    content_version: str
    capability: ResearchCapability


class ResearchReadPort(Protocol):
    """Reference returns only published, quality-accepted versions, scope isolated."""

    def reference(self, scope_id: UUID, publication_id: UUID) -> ResearchPublicationReference: ...
    def manifest(self, scope: UUID, publication_id: UUID) -> ResearchPublicationManifest: ...
    def page(self, query: ResearchQuery) -> ResearchRecordPage: ...
    def processing_failures(
        self, scope: UUID, request: UUID
    ) -> tuple[ResearchProcessingFailure, ...]: ...


class ResearchService:
    def __init__(
        self,
        auth: DataAuthorizer,
        providers: tuple[ResearchProvider, ...],
        reader: ResearchReadPort | None = None,
        *,
        plan_guard: CollectionPlanGuard | None = None,
    ):
        self.auth, self.providers, self.reader = auth, providers, reader
        self.plan_guard = plan_guard or CollectionPlanGuard(
            version="collection-plan-guard.v1",
            max_instruments=10000,
            max_new_parts=20000,
            confirm_new_parts=100,
            confirm_credits=500,
        )

    def catalog(self, actor_id: UUID, scope_id: UUID) -> tuple[ResearchCapability, ...]:
        self.auth.require(actor_id, scope_id, "read")
        capabilities = tuple(c for p in self.providers for c in p.capabilities())
        if len({c.source for c in capabilities}) != len(capabilities):
            raise DataFailure("unverified")
        return capabilities

    def preview(self, request: ResearchPlanRequest) -> ResearchPlan:
        self.auth.require(request.actor_id, request.scope_id, "manage")
        # Check before provider discovery/partitioning. This rejects the whole
        # selection, never silently removes instruments to fit an operator cap.
        if len(request.instrument_ids) > self.plan_guard.max_instruments:
            raise DataFailure("invalid_input")
        matches = [
            (p, c) for p in self.providers for c in p.capabilities() if c.source == request.source
        ]
        if len(matches) != 1:
            raise DataFailure("unsupported")
        provider, capability = matches[0]
        if not capability.configured:
            raise DataFailure("unavailable")
        if request.kind != capability.kind or request.date_axis not in capability.date_axes:
            raise DataFailure("unsupported")
        if (
            capability.history_start
            and request.start < capability.history_start
            or capability.history_end
            and request.end > capability.history_end
        ):
            raise DataFailure("unsupported")
        for identifier in request.instrument_ids:
            kind, market, _, _ = instrument_key(identifier)
            if kind not in capability.instrument_kinds or market not in capability.markets:
                raise DataFailure("unsupported")
        bounded_parts: list[ResearchPartition] = []
        for part in provider.partitions(request):
            # Stream the bounded prefix rather than materializing an arbitrary
            # supplier iterator. At most limit+1 partitions are consumed; no
            # partial plan or job is persisted when the complete range exceeds
            # the limit. Existing frozen plans are not rewritten by this guard.
            if len(bounded_parts) >= self.plan_guard.max_new_parts:
                raise DataFailure("invalid_input")
            bounded_parts.append(part)
        parts = tuple(bounded_parts)
        if not parts or any(p.instrument_id not in request.instrument_ids for p in parts):
            raise DataFailure("unverified")
        for identifier in request.instrument_ids:
            cursor = request.start
            for part in sorted(
                (p for p in parts if p.instrument_id == identifier), key=lambda p: p.start
            ):
                if part.start != cursor or part.end > request.end:
                    raise DataFailure("unverified")
                cursor = part.end
            if cursor != request.end:
                raise DataFailure("unverified")
        version = "research-plan.v1"
        return ResearchPlan(
            request=request,
            capability=capability,
            partitions=parts,
            plan_version=version,
            plan_sha256=digest(
                {
                    "request": request.model_dump(mode="json"),
                    "capability": capability.model_dump(mode="json"),
                    "partitions": [p.model_dump(mode="json") for p in parts],
                    "plan_version": version,
                }
            ),
            estimated_calls_min=len(parts),
            estimated_calls_max=None,
            estimate_basis="每分片至少一次获取；分页/复核与加权信用成本待来源证据，非精确报价",
        )

    def records(self, query: ResearchQuery) -> ResearchRecordPage:
        self.auth.require(query.actor_id, query.scope_id, "read")
        if self.reader is None:
            raise DataFailure("not_found")
        reference = self.reader.reference(query.scope_id, query.publication_id)
        if reference.scope_id != query.scope_id or reference.publication_id != query.publication_id:
            raise DataFailure("forbidden")
        if reference.content_version != query.content_version:
            raise DataFailure("conflict")
        if reference.capability.kind != query.kind:
            raise DataFailure("unsupported")
        if query.as_of is not None and not reference.capability.supports_as_of:
            raise DataFailure("unsupported")
        page = self.reader.page(query)
        if (
            page.scope_id != query.scope_id
            or page.publication_id != query.publication_id
            or page.content_version != query.content_version
            or page.kind != query.kind
            or page.cursor != query.cursor
            or len(page.records) > query.limit
            or any(record.source != reference.capability.source for record in page.records)
        ):
            raise DataFailure("unverified")
        if query.as_of is not None and any(
            record.knowledge.status != "evidenced"
            or record.knowledge.known_at is None
            or record.knowledge.known_at > query.as_of
            for record in page.records
        ):
            raise DataFailure("unverified")
        return page

    def controlled_preview(self, request: ResearchPlanRequest) -> ResearchPlanPreview:
        plan = self.preview(request)
        provider = next(
            p for p in self.providers if any(c.source == request.source for c in p.capabilities())
        )
        estimate = provider.estimate(request, len(plan.partitions))
        body = plan.model_dump(mode="json") | {
            "execution_estimate": estimate.model_dump(mode="json"),
            "plan_guard": self.plan_guard.model_dump(mode="json"),
            "confirmation_required": (
                len(plan.partitions) >= self.plan_guard.confirm_new_parts
                or estimate.credits_max >= self.plan_guard.confirm_credits
            ),
        }
        return ResearchPlanPreview.model_validate_json(
            json.dumps(body | {"control_sha256": digest(body)})
        )

    def manifest(
        self, actor: UUID, scope: UUID, publication_id: UUID
    ) -> ResearchPublicationManifest:
        self.auth.require(actor, scope, "read")
        if self.reader is None:
            raise DataFailure("not_found")
        manifest = self.reader.manifest(scope, publication_id)
        if manifest.scope_id != scope or manifest.publication_id != publication_id:
            raise DataFailure("forbidden")
        return manifest

    def processing_failures(
        self, actor: UUID, scope: UUID, request: UUID
    ) -> tuple[ResearchProcessingFailure, ...]:
        self.auth.require(actor, scope, "read")
        if self.reader is None:
            raise DataFailure("not_found")
        values = self.reader.processing_failures(scope, request)
        if any(f.scope_id != scope or f.request_id != request for f in values):
            raise DataFailure("unverified")
        return values
