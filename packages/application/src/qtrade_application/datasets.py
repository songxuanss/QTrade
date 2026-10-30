"""Replay verified raw batches into a new immutable dataset publication."""

import json
from collections.abc import Sequence
from hashlib import sha256
from uuid import UUID

from qtrade_application.batches import RawBatchService
from qtrade_contracts import Bar, MarketDataset
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.datasets import (
    BarTransformationPort,
    DatasetQualityPort,
    DatasetRepositoryPort,
    PublishedDataset,
    ReplayRequest,
)
from qtrade_domain.market_data import dataset_bars, dataset_digest
from qtrade_domain.ports import ErrorCode, MarketDataPort, ObjectRef, ObjectStorePort, PortError


class DatasetReplayService:
    def __init__(
        self,
        repository: DatasetRepositoryPort,
        batches: RawBatchService,
        market: MarketDataPort,
        objects: ObjectStorePort,
        transformations: Sequence[BarTransformationPort],
        assessments: Sequence[DatasetQualityPort] = (),
    ):
        self._repository, self._batches = repository, batches
        self._market, self._objects = market, objects
        self._transformations = {item.version: item for item in transformations}
        self._assessments = {item.transformation_version: item for item in assessments}
        if len(self._assessments) != len(assessments):
            raise ValueError("duplicate dataset assessment")
        if len(self._transformations) != len(transformations):
            raise ValueError("duplicate transformation version")

    def _get(self, workspace_id: UUID, dataset_id: UUID) -> PublishedDataset:
        result = self._repository.get(workspace_id, dataset_id)
        if (
            not isinstance(result, PublishedDataset)
            or result.metadata.workspace_id != workspace_id
            or result.metadata.dataset_id != dataset_id
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "dataset repository returned invalid scope")
        return result

    def read(self, access: WorkspaceAccess, dataset_id: UUID) -> PublishedDataset:
        access.require(Permission.READ)
        result = self._get(access.workspace.workspace_id, dataset_id)
        try:
            report = self._objects.get(
                result.metadata.workspace_id,
                result.metadata.quality_report_key,
                expected_sha256=result.quality_report_sha256,
            )
        except PortError as error:
            if error.code == ErrorCode.NOT_FOUND:
                raise PortError(ErrorCode.UNAVAILABLE, "dataset report unavailable") from None
            raise
        if (
            not isinstance(report, bytes)
            or sha256(report).hexdigest() != result.quality_report_sha256
        ):
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "dataset report checksum mismatch")
        return result

    def replay(self, access: WorkspaceAccess, request: ReplayRequest) -> PublishedDataset:
        access.require(Permission.DATA_MANAGE, object_workspace_id=request.workspace_id)
        try:
            existing = self._get(request.workspace_id, request.dataset_id)
        except PortError as error:
            if error.code != ErrorCode.NOT_FOUND:
                raise
        else:
            if existing.request_sha256 != request.fingerprint:
                raise PortError(ErrorCode.CONFLICT, "dataset identity reused by another replay")
            return self.read(access, request.dataset_id)
        transformation = self._transformations.get(request.transformation_version)
        if transformation is None or transformation.version != request.transformation_version:
            raise PortError(ErrorCode.INVALID_INPUT, "unsupported transformation version")
        version = 1
        if request.parent_dataset_id is not None:
            parent = self.read(access, request.parent_dataset_id).metadata
            if parent.batch_ids != request.batch_ids:
                raise PortError(ErrorCode.INVALID_INPUT, "replay must retain parent source batches")
            version = parent.version + 1
        batches = tuple(self._batches.read(access, batch_id) for batch_id in request.batch_ids)
        granularities = {batch.metadata.granularity for batch in batches}
        if len(granularities) != 1:
            raise PortError(ErrorCode.INVALID_INPUT, "replay requires one source granularity")
        output_granularity = transformation.output_granularity(batches[0].metadata.granularity)
        transformed = transformation.transform(request.dataset_id, batches)
        bars = dataset_bars(request.workspace_id, request.dataset_id, transformed)
        sources = {batch.metadata.batch_id: batch.metadata for batch in batches}
        for bar in bars:
            try:
                Bar.model_validate_json(bar.model_dump_json())
            except (ValueError, TypeError):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid transformed Bar") from None
            source = sources.get(bar.batch_id)
            if (
                source is None
                or source.instrument_id != bar.instrument_id
                or output_granularity != bar.granularity
                or not request.start <= bar.timestamp < request.end
            ):
                raise PortError(
                    ErrorCode.INVALID_INPUT, "transformed Bar provenance or range mismatch"
                )
        digest = dataset_digest(bars)
        assessment = self._assessments.get(request.transformation_version)
        coverage = (
            assessment.assess(request, batches, bars)
            if assessment
            else {
                "research_readiness": "unverified",
                "coverage_status": "unverified",
                "limitations": ["Source completeness and research prerequisites are not verified."],
            }
        )
        report = json.dumps(
            {
                "coverage": coverage,
                "schema_version": 1,
                "workspace_id": str(request.workspace_id),
                "dataset_id": str(request.dataset_id),
                "transformation_version": request.transformation_version,
                "content_sha256": digest,
                "source_batches": [batch.metadata.model_dump(mode="json") for batch in batches],
                "raw_record_count": sum(len(batch.records) for batch in batches),
                "bar_count": len(bars),
                "quality_counts": {
                    quality: sum(bar.quality == quality for bar in bars)
                    for quality in ("valid", "suspect", "quarantined")
                },
                "checks": ["bar-contract", "source-provenance", "unique-bar", "requested-range"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        report_sha = sha256(report).hexdigest()
        key = f"datasets/{request.dataset_id}/quality/{report_sha}.json"
        ref = self._objects.put(
            request.workspace_id,
            key,
            report,
            content_type="application/json",
            expected_sha256=report_sha,
        )
        if (
            not isinstance(ref, ObjectRef)
            or ref.workspace_id != request.workspace_id
            or ref.key != key
            or ref.content_sha256 != report_sha
            or ref.size != len(report)
            or ref.content_type != "application/json"
        ):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid dataset report reference")
        try:
            verified = self._objects.get(request.workspace_id, key, expected_sha256=report_sha)
        except PortError as error:
            if error.code == ErrorCode.NOT_FOUND:
                raise PortError(
                    ErrorCode.UNAVAILABLE, "dataset report publication unavailable"
                ) from None
            raise
        if verified != report:
            raise PortError(
                ErrorCode.CHECKSUM_MISMATCH, "dataset report publication checksum mismatch"
            )
        metadata = MarketDataset(
            schema_version=1,
            workspace_id=request.workspace_id,
            dataset_id=request.dataset_id,
            version=version,
            created_at=request.created_at,
            batch_ids=request.batch_ids,
            instrument_ids=tuple(sorted({batch.metadata.instrument_id for batch in batches})),
            granularity=output_granularity,
            start=request.start,
            end=request.end,
            transformation_version=request.transformation_version,
            quality_report_key=key,
            content_sha256=digest,
        )
        published = PublishedDataset(
            metadata, request.fingerprint, request.parent_dataset_id, report_sha
        )
        # Committed metadata is the visibility boundary. Failed publication leaves immutable
        # candidates; exact retries reuse the same object and market snapshot.
        self._market.write_bars(request.workspace_id, request.dataset_id, bars)
        self._repository.put(access.user_id, published)
        return published

    def quality_report(self, access: WorkspaceAccess, dataset_id: UUID) -> dict[str, object]:
        published = self.read(access, dataset_id)
        content = self._objects.get(
            published.metadata.workspace_id,
            published.metadata.quality_report_key,
            expected_sha256=published.quality_report_sha256,
        )
        if (
            not isinstance(content, bytes)
            or sha256(content).hexdigest() != published.quality_report_sha256
        ):
            raise PortError(ErrorCode.CHECKSUM_MISMATCH, "dataset report checksum mismatch")
        try:
            report = json.loads(content)
            if not isinstance(report, dict) or report.get("dataset_id") != str(dataset_id):
                raise ValueError
        except (TypeError, ValueError):
            raise PortError(ErrorCode.UNAVAILABLE, "invalid quality report") from None
        return report
