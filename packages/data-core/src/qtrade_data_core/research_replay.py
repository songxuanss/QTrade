"""Bounded offline RAW execution; supplier fetch/partition methods are never used."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid5

from qtrade_data_contracts.collection import digest
from qtrade_data_contracts.research import ResearchObservation
from qtrade_data_contracts.research_archive import ResearchRawManifest
from qtrade_data_contracts.research_replay import (
    ResearchReplay,
    ResearchReplayLimits,
    ResearchReplayRequest,
)
from qtrade_data_contracts.research_transport import ResearchPlan
from qtrade_data_core.ports import DataAuthorizer, DataFailure
from qtrade_data_core.research import ResearchProvider, ResearchRawPage
from qtrade_data_core.research_collection import ResearchCollectionResult, normalize_page
from qtrade_data_core.research_evidence import ResearchArchivePort, ResearchEvidenceRepository


class ReplayArchive(ResearchArchivePort, Protocol):
    def replay_raw(
        self, manifest: ResearchRawManifest, maximum: int
    ) -> tuple[tuple[dict[str, object], ...], int]: ...


class ReplayRepository(ResearchEvidenceRepository, Protocol):
    def plan(self, scope: UUID, request: UUID) -> ResearchPlan: ...
    def raw_inputs(
        self, scope: UUID, request: UUID, limit: int
    ) -> tuple[ResearchRawManifest, ...]: ...
    def replay(self, scope: UUID, request: UUID) -> ResearchReplay | None: ...
    def enqueue_replay(self, value: ResearchReplay) -> None: ...
    def job(self, scope: UUID, request: UUID) -> dict[str, object]: ...


def ordered_inputs(
    plan: ResearchPlan, inputs: tuple[ResearchRawManifest, ...]
) -> tuple[ResearchRawManifest, ...]:
    """Require every frozen partition and one complete, connected cursor chain.

    Sorting by batch UUID is not page order. Never fill missing pages from the
    network, nor choose arbitrarily among conflicting versions of one cursor.
    """
    groups: dict[tuple[str, object, object], list[ResearchRawManifest]] = {}
    for raw in inputs:
        if (raw.scope_id, raw.request_id, raw.plan_sha256, raw.source, raw.kind) != (
            plan.request.scope_id,
            plan.request.request_id,
            plan.plan_sha256,
            plan.capability.source,
            plan.capability.kind,
        ):
            raise DataFailure("unverified")
        groups.setdefault((raw.instrument_id, raw.start, raw.end), []).append(raw)
    expected = [(p.instrument_id, p.start, p.end) for p in plan.partitions]
    if len(set(expected)) != len(expected) or set(groups) != set(expected):
        raise DataFailure("unverified")
    result: list[ResearchRawManifest] = []
    for key in expected:
        pages = groups[key]
        by_cursor = {b.cursor: b for b in pages}
        if len(by_cursor) != len(pages):
            raise DataFailure("unverified")
        cursor = None
        seen: set[str | None] = set()
        while True:
            if cursor in seen or cursor not in by_cursor:
                raise DataFailure("unverified")
            seen.add(cursor)
            raw = by_cursor[cursor]
            result.append(raw)
            cursor = raw.next_cursor
            if cursor is None:
                if len(seen) != len(pages):
                    raise DataFailure("unverified")
                break
    return tuple(result)


class ResearchReplayService:
    def __init__(
        self,
        auth: DataAuthorizer,
        archive: ReplayArchive,
        repository: ReplayRepository,
        providers: tuple[ResearchProvider, ...],
        *,
        limits: ResearchReplayLimits | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.auth, self.archive, self.repository, self.providers = (
            auth,
            archive,
            repository,
            providers,
        )
        self.limits, self.clock = limits or ResearchReplayLimits(), clock

    def _provider(self, plan: ResearchPlan) -> ResearchProvider:
        matches = [p for p in self.providers if plan.capability in p.capabilities()]
        if len(matches) != 1:
            raise DataFailure("unsupported")
        return matches[0]

    def preview(self, command: ResearchReplayRequest) -> ResearchReplay:
        self.auth.require(command.actor_id, command.scope_id, "manage")
        parent = self.repository.plan(command.scope_id, command.parent_request_id)
        if (parent.request.scope_id, parent.request.request_id) != (
            command.scope_id,
            command.parent_request_id,
        ):
            raise DataFailure("unverified")
        capabilities = [
            c
            for p in self.providers
            for c in p.capabilities()
            if (c.source, c.kind) == (parent.request.source, parent.request.kind)
        ]
        if len(capabilities) != 1:
            raise DataFailure("unsupported")
        capability = capabilities[0]
        # This is a local normalization intent. We deliberately do not call
        # partitions/preview/fetch or require a configured network credential.
        request = parent.request.model_copy(
            update={
                "actor_id": command.actor_id,
                "request_id": command.request_id,
                "correlation_id": uuid5(command.request_id, "research-replay"),
                "mode": "all",
            }
        )
        fields = dict(
            request=request.model_dump(mode="json"),
            capability=capability.model_dump(mode="json"),
            partitions=[p.model_dump(mode="json") for p in parent.partitions],
            plan_version="research-replay-plan.v1",
        )
        target = ResearchPlan.model_validate_json(
            json.dumps(
                fields
                | {
                    "plan_sha256": digest(fields),
                    "estimated_calls_min": 0,
                    "estimated_calls_max": 0,
                    "estimate_basis": "离线重放完整RAW归档，不访问供应商；本地转换和存储成本另计。",
                }
            )
        )
        inputs = self.repository.raw_inputs(
            command.scope_id, command.parent_request_id, self.limits.max_pages + 1
        )
        if not inputs or len(inputs) > self.limits.max_pages:
            raise DataFailure("invalid_input")
        inputs = ordered_inputs(parent, inputs)
        if sum(b.raw_record_count for b in inputs) > self.limits.max_rows:
            raise DataFailure("invalid_input")
        # Check actual bytes in the read-only preview, without inventing a
        # standard count. Execution repeats integrity and current-limit checks.
        remaining = self.limits.max_bytes
        for raw in inputs:
            _, size = self.archive.replay_raw(raw, remaining)
            remaining -= size
        body = dict(
            schema_version=2,
            replay_version="research-raw-replay.v1",
            command=command.model_dump(mode="json"),
            parent_plan=parent.model_dump(mode="json"),
            target_plan=target.model_dump(mode="json"),
            source_batches=[b.model_dump(mode="json") for b in inputs],
            limits=self.limits.model_dump(mode="json"),
        )
        return ResearchReplay.model_validate_json(
            json.dumps(body | {"control_sha256": digest(body)})
        )

    def submit(self, command: ResearchReplayRequest, control: str) -> dict[str, object]:
        self.auth.require(command.actor_id, command.scope_id, "manage")
        existing = self.repository.replay(command.scope_id, command.request_id)
        if existing is not None:
            if existing.command != command or existing.control_sha256 != control:
                raise DataFailure("conflict")
            return self.repository.job(command.scope_id, command.request_id)
        value = self.preview(command)
        if value.control_sha256 != control:
            raise DataFailure("conflict")
        self.auth.require(command.actor_id, command.scope_id, "manage")
        self.repository.enqueue_replay(value)
        return self.repository.job(command.scope_id, command.request_id)

    def read(self, actor: UUID, scope: UUID, request: UUID) -> ResearchReplay:
        self.auth.require(actor, scope, "read")
        value = self.repository.replay(scope, request)
        if value is None:
            raise DataFailure("not_found")
        return value

    def execute(self, plan: ResearchPlan, value: ResearchReplay) -> ResearchCollectionResult:
        """Local work only; copies immutable inputs under a new intent identity.

        Source RAW and collected_at remain unchanged. The replay lineage records
        where the copied bytes came from; its timestamp never creates PIT proof.
        """
        req = plan.request
        self.auth.require(req.actor_id, req.scope_id, "manage")
        value = ResearchReplay.model_validate_json(value.model_dump_json())
        if value.target_plan != plan:
            raise DataFailure("conflict")
        provider = self._provider(plan)
        maximum_pages = min(value.limits.max_pages, self.limits.max_pages)
        maximum_rows = min(value.limits.max_rows, self.limits.max_rows)
        remaining = min(value.limits.max_bytes, self.limits.max_bytes)
        if (
            len(value.source_batches) > maximum_pages
            or sum(b.raw_record_count for b in value.source_batches) > maximum_rows
        ):
            raise DataFailure("invalid_input")
        batches: list[ResearchRawManifest] = []
        records: list[ResearchObservation] = []
        for original in ordered_inputs(value.parent_plan, value.source_batches):
            self.auth.require(req.actor_id, req.scope_id, "manage")
            if self.repository.raw_manifest(req.scope_id, original.batch_id) != original:
                raise DataFailure("unverified")
            rows, size = self.archive.replay_raw(original, remaining)
            remaining -= size
            page = ResearchRawPage(rows, original.cursor, original.next_cursor)
            partition = next(
                p
                for p in plan.partitions
                if (p.instrument_id, p.start, p.end)
                == (original.instrument_id, original.start, original.end)
            )
            raw = self.archive.capture(plan, partition, page, original.collected_at)
            self.repository.append_raw(raw)
            batches.append(raw)
            records.extend(normalize_page(self.archive, plan, provider, page, raw, self.clock))
        self.auth.require(req.actor_id, req.scope_id, "manage")
        if not records:
            return ResearchCollectionResult(tuple(batches), None, "no_observations")
        artifact = self.archive.standardize(plan, tuple(batches), tuple(records), self.clock())
        self.repository.append_artifact(artifact)
        return ResearchCollectionResult(tuple(batches), artifact, "standard_persisted")
