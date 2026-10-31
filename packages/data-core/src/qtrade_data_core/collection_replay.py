"""Bounded offline intents: authorize, freeze, enqueue, normalize and retain lineage."""

import json
from dataclasses import replace
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from qtrade_data_contracts.collection import (
    CollectionJobRequest,
    CollectionPublication,
    CollectionRawCapture,
    SourceDataset,
    digest,
    instrument_key,
)
from qtrade_data_contracts.collection_quality import CollectionQualityRuleVersion
from qtrade_data_contracts.collection_replay import (
    CollectionInputReference,
    CollectionReplay,
    CollectionReplayInputs,
    CollectionReplayLimits,
    CollectionReplayParent,
    CollectionReplayRequest,
    CollectionReplaySource,
)
from qtrade_data_contracts.normalization_input import (
    CollectionNormalizationInput,
    FactorNormalizationInput,
)
from qtrade_data_core.collection import CollectionPart, CollectionProvider, CollectionResult
from qtrade_data_core.collection_quality import RULE_VERSION, evaluator_for
from qtrade_data_core.normalization_input import PreparedCollectionProvider
from qtrade_data_core.ports import DataAuthorizer, DataFailure


class ReplayArchive(Protocol):
    def normalization_inputs(
        self, part: CollectionPart
    ) -> tuple[CollectionNormalizationInput, ...]: ...
    def source_row_proofs(
        self, part: CollectionPart, before: datetime
    ) -> tuple[tuple[CollectionRawCapture, frozenset[str]], ...]: ...
    def normalization_evidence(
        self, part: CollectionPart, input_id: UUID, maximum: int, max_receipts: int
    ) -> tuple[CollectionNormalizationInput, tuple[CollectionRawCapture, ...], int]: ...


class ReplayRepository(Protocol):
    def part_for_job(self, scope: UUID, job: UUID, part: UUID) -> CollectionPart: ...
    def replay(self, scope: UUID, request: UUID) -> CollectionReplay | None: ...
    def enqueue_replay(self, value: CollectionReplay) -> dict[str, object]: ...
    def job(self, scope: UUID, job: UUID) -> dict[str, object]: ...


def parent_reference(part: CollectionPart) -> CollectionReplayParent:
    return CollectionReplayParent(
        request=part.request,
        job_id=part.job_id,
        part_id=part.part_id,
        instrument_id=part.instrument_id,
        field=part.field,
        start=part.start,
        end=part.end,
        mapping_version=part.mapping_version,
        instrument_name=part.instrument_name,
        execution_kind=part.execution_kind,
    )


def input_reference(value: CollectionNormalizationInput) -> CollectionInputReference:
    return CollectionInputReference(
        scope_id=value.request.scope_id,
        owner_job_id=value.job_id,
        part_id=value.part_id,
        input_id=value.input_id,
        input_sha256=value.input_sha256,
        instrument_id=value.instrument_id,
        instrument_name=value.instrument_name,
        field=value.field,
        source=value.request.source,
        dataset_kind=value.request.dataset_kind,
        granularity=value.request.granularity,
        start=value.start,
        end=value.end,
        mapping_version=value.mapping_version,
        prepared_at=value.prepared_at,
        row_count=(
            len(value.payload.rows)
            if isinstance(value.payload, FactorNormalizationInput)
            else sum(len(s.rows) for s in value.payload.slices)
        ),
        factor_definition=value.payload.definition
        if isinstance(value.payload, FactorNormalizationInput)
        else None,
    )


def lineage(value: CollectionReplay) -> dict[str, object]:
    return {
        "request_id": str(value.command.request_id),
        "parent_job_id": str(value.command.parent_job_id),
        "parent_part_id": str(value.command.parent_part_id),
        "input_id": str(value.input.input_id),
        "input_sha256": value.input.input_sha256,
        "rule_version": value.rule_version,
        "original_acquired_at": sorted(
            {
                s.captured_at.isoformat()
                for s in value.sources
                if s.kind in {"history", "factor_history"}
            }
        ),
        "original_receipt_at": sorted({s.captured_at.isoformat() for s in value.sources}),
    }


def target_part(value: CollectionReplay) -> CollectionPart:
    return CollectionPart(
        value.target_part_id,
        value.command.request_id,
        value.target_request,
        value.input.instrument_id,
        value.input.field,
        value.input.start,
        value.input.end,
        value.target_mapping_version,
        value.input.instrument_name,
        execution_kind="normalization_replay",
    )


class CollectionReplayService:
    def __init__(
        self,
        auth: DataAuthorizer,
        archive: ReplayArchive,
        repository: ReplayRepository,
        providers: tuple[CollectionProvider, ...],
        *,
        limits: CollectionReplayLimits | None = None,
    ):
        self.auth, self.archive, self.repository, self.providers = (
            auth,
            archive,
            repository,
            providers,
        )
        self.limits = limits or CollectionReplayLimits()

    def _parent(self, scope: UUID, job: UUID, part_id: UUID) -> CollectionPart:
        value = self.repository.part_for_job(scope, job, part_id)
        if value.request.scope_id != scope or value.part_id != part_id:
            raise DataFailure("unverified")
        return value

    def _stored(self, scope: UUID, request: UUID) -> CollectionReplay | None:
        value = self.repository.replay(scope, request)
        if value is not None:
            try:
                value = CollectionReplay.model_validate_json(value.model_dump_json())
            except ValueError:
                raise DataFailure("unverified") from None
            if (value.command.scope_id, value.command.request_id) != (scope, request):
                raise DataFailure("unverified")
        return value

    def _root(self, part: CollectionPart, input_id: UUID | None = None) -> CollectionPart:
        if part.execution_kind == "collection":
            return part
        value = self._stored(part.request.scope_id, part.job_id)
        if value is None or target_part(value) != replace(
            part, state="queued", attempt=0, lease_token=None
        ):
            raise DataFailure("unverified")
        if input_id is not None and input_id != value.input.input_id:
            raise DataFailure("conflict")
        # Intents retain the root input directly. Repeated replay does not walk
        # an unbounded ancestry chain or reinterpret a copied acquisition time.
        return self._parent(value.input.scope_id, value.input.owner_job_id, value.input.part_id)

    def inputs(
        self, actor: UUID, scope: UUID, job: UUID, part_id: UUID
    ) -> tuple[CollectionInputReference, ...]:
        self.auth.require(actor, scope, "read")
        parent = self._parent(scope, job, part_id)
        root = self._root(parent)
        if parent.execution_kind == "normalization_replay":
            value = self._stored(scope, parent.job_id)
            assert value is not None
            return (value.input,)
        return tuple(input_reference(v) for v in self.archive.normalization_inputs(root))

    def catalog(self, actor: UUID, scope: UUID, job: UUID, part_id: UUID) -> CollectionReplayInputs:
        values = self.inputs(actor, scope, job, part_id)
        parent = self._parent(scope, job, part_id)
        return CollectionReplayInputs(
            scope_id=scope,
            job_id=job,
            part_id=part_id,
            parent=parent_reference(parent),
            inputs=values,
        )

    def _provider(self, ref: CollectionInputReference) -> tuple[CollectionProvider, SourceDataset]:
        matches = [
            (p, d)
            for p in self.providers
            for d in p.datasets()
            if d.source == ref.source
            and d.dataset_kind == ref.dataset_kind
            and ref.granularity in d.periods
        ]
        if len(matches) != 1 or not isinstance(matches[0][0], PreparedCollectionProvider):
            raise DataFailure("unsupported")
        provider, dataset = matches[0]
        kind, market, _, _ = instrument_key(ref.instrument_id)
        if kind not in dataset.instrument_kinds or market not in dataset.markets:
            raise DataFailure("unsupported")
        try:
            ZoneInfo(dataset.timezone)
        except (ValueError, KeyError):
            raise DataFailure("unverified") from None
        return provider, dataset

    def preview(
        self,
        command: CollectionReplayRequest,
        *,
        rule_version: CollectionQualityRuleVersion = RULE_VERSION,
    ) -> CollectionReplay:
        self.auth.require(command.actor_id, command.scope_id, "manage")
        evaluator_for(rule_version)
        parent = self._parent(command.scope_id, command.parent_job_id, command.parent_part_id)
        root = self._root(parent, command.input_id)
        value, receipts, size = self.archive.normalization_evidence(
            root, command.input_id, self.limits.max_bytes, self.limits.max_receipts
        )
        if command.request_id == root.job_id:
            raise DataFailure("invalid_input")
        existing = self._stored(command.scope_id, command.request_id)
        if existing is not None and existing.command != command:
            raise DataFailure("conflict")
        if existing is None:
            try:
                self.repository.job(command.scope_id, command.request_id)
            except DataFailure as error:
                if error.code != "not_found":
                    raise
            else:
                raise DataFailure("conflict")
        ref = input_reference(value)
        if ref.row_count > self.limits.max_rows:
            raise DataFailure("invalid_input")
        _, dataset = self._provider(ref)
        request = CollectionJobRequest(
            schema_version=1,
            scope_id=command.scope_id,
            actor_id=command.actor_id,
            request_id=command.request_id,
            correlation_id=uuid5(command.request_id, "normalization-replay"),
            source=ref.source,
            dataset_kind=ref.dataset_kind,
            granularity=ref.granularity,
            instrument_ids=(ref.instrument_id,),
            fields=(ref.field,) if ref.field else (),
            start=ref.start,
            end=ref.end,
            mode="all",
        )
        body = dict(
            schema_version=2,
            replay_version="collection-normalization-replay.v1",
            command=command.model_dump(mode="json"),
            parent=parent_reference(parent).model_dump(mode="json"),
            input=ref.model_dump(mode="json"),
            sources=[
                CollectionReplaySource(
                    capture_id=r.capture_id,
                    kind=r.kind,
                    content_sha256=r.content_sha256,
                    captured_at=r.captured_at,
                    response_rows=r.response_rows,
                ).model_dump(mode="json")
                for r in receipts
            ],
            target_request=request.model_dump(mode="json"),
            target_part_id=str(uuid5(command.request_id, "normalization-replay")),
            target_mapping_version=dataset.mapping_version,
            rule_version=rule_version,
            timezone=dataset.timezone,
            verified_bytes=size,
            limits=self.limits.model_dump(mode="json"),
            supplier_calls=0,
        )
        return CollectionReplay.model_validate_json(
            json.dumps(body | {"control_sha256": digest(body)})
        )

    def submit(self, command: CollectionReplayRequest, control: str) -> dict[str, object]:
        self.auth.require(command.actor_id, command.scope_id, "manage")
        old = self._stored(command.scope_id, command.request_id)
        if old is not None:
            if old.command != command or old.control_sha256 != control:
                raise DataFailure("conflict")
            return self.repository.job(command.scope_id, command.request_id)
        value = self.preview(command)
        if value.control_sha256 != control:
            raise DataFailure("conflict")
        self.auth.require(command.actor_id, command.scope_id, "manage")
        return self.repository.enqueue_replay(value)

    def read(self, actor: UUID, scope: UUID, request: UUID) -> CollectionReplay:
        self.auth.require(actor, scope, "read")
        value = self._stored(scope, request)
        if value is None:
            raise DataFailure("not_found")
        return value

    def proofs(
        self, actor: UUID, part: CollectionPart, publication: CollectionPublication
    ) -> tuple[tuple[CollectionRawCapture, frozenset[str]], ...]:
        self.auth.require(actor, part.request.scope_id, "read")
        value = self._stored(part.request.scope_id, part.job_id)
        if (
            value is None
            or target_part(value) != replace(part, state="queued", attempt=0, lease_token=None)
            or publication.evidence.get("normalization_replay") != lineage(value)
        ):
            raise DataFailure("unverified")
        root = self._parent(value.input.scope_id, value.input.owner_job_id, value.input.part_id)
        frozen, receipts, size = self.archive.normalization_evidence(
            root,
            value.input.input_id,
            min(value.limits.max_bytes, self.limits.max_bytes),
            min(value.limits.max_receipts, self.limits.max_receipts),
        )
        actual = tuple(
            CollectionReplaySource(
                capture_id=r.capture_id,
                kind=r.kind,
                content_sha256=r.content_sha256,
                captured_at=r.captured_at,
                response_rows=r.response_rows,
            )
            for r in receipts
        )
        if (
            input_reference(frozen) != value.input
            or actual != value.sources
            or size != value.verified_bytes
        ):
            raise DataFailure("unverified")
        ids = {s.capture_id for s in value.sources}
        return tuple(
            (c, rows)
            for c, rows in self.archive.source_row_proofs(root, publication.published_at)
            if c.capture_id in ids
        )

    def execute(
        self, part: CollectionPart
    ) -> tuple[CollectionResult, str, CollectionQualityRuleVersion]:
        self.auth.require(part.request.actor_id, part.request.scope_id, "manage")
        value = self._stored(part.request.scope_id, part.job_id)
        if value is None or target_part(value) != replace(
            part, state="queued", attempt=0, lease_token=None
        ):
            raise DataFailure("unverified")
        # Rebuild with today's authorization, stricter limits and registered
        # mapper/rule. Any drift rejects; it never fetches replacement data.
        current = self.preview(value.command, rule_version=value.rule_version)
        if current.model_dump(exclude={"control_sha256", "limits"}) != value.model_dump(
            exclude={"control_sha256", "limits"}
        ):
            raise DataFailure("conflict")
        root = self._parent(value.input.scope_id, value.input.owner_job_id, value.input.part_id)
        frozen, _, _ = self.archive.normalization_evidence(
            root,
            value.input.input_id,
            min(value.limits.max_bytes, self.limits.max_bytes),
            min(value.limits.max_receipts, self.limits.max_receipts),
        )
        provider, _ = self._provider(value.input)
        assert isinstance(provider, PreparedCollectionProvider)
        self.auth.require(part.request.actor_id, part.request.scope_id, "manage")
        result = provider.normalize(part, frozen.payload)
        self.auth.require(part.request.actor_id, part.request.scope_id, "manage")
        if len(result.records) > min(value.limits.max_rows, self.limits.max_rows):
            raise DataFailure("invalid_input")
        result = replace(
            result,
            evidence=result.evidence | {"normalization_replay": lineage(value)},
            limitations=result.limitations
            + ("离线重处理原归档；本次未获取供应商新数据，原可知时间与研究限制保持。",),
        )
        return result, value.timezone, value.rule_version
