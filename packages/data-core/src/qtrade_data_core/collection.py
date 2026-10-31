"""Portable collection orchestration: plans, leases, archive-before-publication and retry."""

import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Literal, Protocol
from uuid import UUID, uuid4, uuid5

from qtrade_data_contracts.collection import (
    CollectedBar,
    CollectionJobRequest,
    CollectionPreview,
    CollectionPublication,
    CollectionRawCapture,
    FactorDefinition,
    FactorPoint,
    RawPayloadKind,
    SourceDataset,
    digest,
    instrument_key,
)
from qtrade_data_contracts.collection_quality import (
    CollectionQualityResult,
    CollectionQualityRuleVersion,
)
from qtrade_data_contracts.normalization_input import NormalizationPayload
from qtrade_data_contracts.planning import CollectionCostEstimate, CollectionPlanGuard
from qtrade_data_contracts.processing_failure import CollectionProcessingFailure
from qtrade_data_core.collection_quality import RULE_VERSION, evaluate
from qtrade_data_core.diagnostics import collection_context
from qtrade_data_core.ports import DataAuthorizer, DataFailure
from qtrade_data_core.schedule_approval import ScheduleExecutionApproval
from qtrade_data_core.usage import DenialReason, UsageDenied
from qtrade_data_core.usage_execution import UsageIdentity, usage_identity, usage_scope

logger = logging.getLogger(__name__)
RawCapture = Callable[[RawPayloadKind, object], None]


@dataclass(frozen=True)
class CollectionPart:
    part_id: UUID
    job_id: UUID
    request: CollectionJobRequest
    instrument_id: str
    field: str | None
    start: datetime
    end: datetime
    mapping_version: str
    instrument_name: str = "名称未核验"
    state: str = "queued"
    attempt: int = 0
    lease_token: UUID | None = None
    execution_kind: Literal["collection", "normalization_replay"] = "collection"

    @property
    def coverage_key(self) -> str:
        return digest(
            [
                self.request.source,
                self.request.dataset_kind,
                self.instrument_id,
                self.request.granularity,
                self.field,
                self.mapping_version,
                "unadjusted",
            ]
        )


@dataclass(frozen=True)
class CollectionResult:
    raw: tuple[dict[str, object], ...]
    records: tuple[CollectedBar | FactorPoint, ...]
    units: dict[str, str]
    source_time_convention: str
    evidence: dict[str, object]
    limitations: tuple[str, ...] = ()


class CollectionProvider(Protocol):
    def estimate(self, request: CollectionJobRequest, new_parts: int) -> CollectionCostEstimate:
        """Estimate future execution only, without HTTP or persistent mutations."""
        ...

    def names(self, identifiers: tuple[str, ...]) -> dict[str, str]: ...
    def datasets(self) -> tuple[SourceDataset, ...]: ...
    def factors(self) -> tuple[FactorDefinition, ...]: ...
    def partitions(
        self, request: CollectionJobRequest, start: datetime, end: datetime
    ) -> Iterator[tuple[datetime, datetime]]: ...
    def fetch(self, part: CollectionPart, *, capture: RawCapture) -> CollectionResult: ...


class CollectionArchive(Protocol):
    def processing_failures(
        self, part: CollectionPart
    ) -> tuple[CollectionProcessingFailure, ...]: ...
    def record_processing_failure(
        self, part: CollectionPart, failure: CollectionProcessingFailure
    ) -> None: ...
    def qualities(self, part: CollectionPart) -> tuple[CollectionQualityResult, ...]: ...
    def capture_raw(
        self, part: CollectionPart, kind: RawPayloadKind, payload: object
    ) -> CollectionRawCapture: ...
    def record_quality(self, part: CollectionPart, result: CollectionQualityResult) -> None: ...
    def recover(self, part: CollectionPart) -> CollectionPublication | None: ...
    def publish(self, part: CollectionPart, result: CollectionResult) -> CollectionPublication: ...
    def records(
        self, publication: CollectionPublication
    ) -> tuple[CollectedBar | FactorPoint, ...]: ...


class JobRepository(Protocol):
    def part_for_job(self, scope_id: UUID, job_id: UUID, part_id: UUID) -> CollectionPart: ...
    def atomic(self, scope_id: UUID) -> AbstractContextManager["JobRepository"]: ...
    def find(self, scope_id: UUID, request_id: UUID) -> dict[str, object] | None: ...
    def coverage(self, part: CollectionPart) -> tuple[CollectionPart, ...]: ...
    def submit(
        self,
        job_id: UUID,
        request: CollectionJobRequest,
        parts: tuple[CollectionPart, ...],
        definitions: tuple[FactorDefinition, ...],
        names: dict[str, str],
    ) -> dict[str, object]: ...
    def job(self, scope_id: UUID, job_id: UUID) -> dict[str, object]: ...
    def jobs(self, scope_id: UUID) -> list[dict[str, object]]: ...
    def claim(self, now: datetime) -> CollectionPart | None: ...
    def complete(self, part: CollectionPart, publication: CollectionPublication | None) -> None: ...
    def fail(self, part: CollectionPart, code: str, retry_at: datetime | None) -> None: ...
    def wait_budget(
        self, part: CollectionPart, reason: DenialReason, retry_at: datetime | None
    ) -> None: ...
    def control(self, scope_id: UUID, job_id: UUID, action: str) -> None: ...
    def publication(self, scope_id: UUID, publication_id: UUID) -> CollectionPublication: ...


def subtract(
    start: datetime, end: datetime, occupied: tuple[CollectionPart, ...]
) -> Iterator[tuple[datetime, datetime]]:
    cursor = start
    for part in sorted(occupied, key=lambda p: (p.start, p.end)):
        if part.start > cursor:
            yield cursor, min(part.start, end)
        cursor = max(cursor, part.end)
        if cursor >= end:
            return
    if cursor < end:
        yield cursor, end


class CollectionReplayExecutor(Protocol):
    def execute(
        self, part: CollectionPart
    ) -> tuple[CollectionResult, str, CollectionQualityRuleVersion]: ...


class CollectionService:
    def __init__(
        self,
        auth: DataAuthorizer,
        repository: JobRepository,
        archive: CollectionArchive,
        providers: tuple[CollectionProvider, ...],
        plan_guard: CollectionPlanGuard | None = None,
        *,
        replays: CollectionReplayExecutor | None = None,
    ):
        self.auth, self.repository, self.archive = auth, repository, archive
        self.providers = providers
        self.replays = replays
        self.plan_guard = plan_guard or CollectionPlanGuard(
            version="collection-plan-guard.v1",
            max_instruments=10000,
            max_new_parts=20000,
            confirm_new_parts=100,
            confirm_credits=500,
        )

    def catalog(self) -> tuple[SourceDataset, ...]:
        note = (
            f"单次最多{self.plan_guard.max_instruments}个标的、"
            f"{self.plan_guard.max_new_parts}个新增分段；超限需缩小范围，不截断日期。"
        )
        return tuple(
            dataset.model_copy(update={"limitations": (*dataset.limitations, note)})
            for provider in self.providers
            for dataset in provider.datasets()
        )

    def _source(self, request: CollectionJobRequest) -> tuple[CollectionProvider, SourceDataset]:
        for provider in self.providers:
            for dataset in provider.datasets():
                if dataset.source != request.source:
                    continue
                if not dataset.configured:
                    raise DataFailure("unavailable")
                if (
                    dataset.dataset_kind != request.dataset_kind
                    or request.granularity not in dataset.periods
                ):
                    raise DataFailure("unsupported")
                for instrument in request.instrument_ids:
                    kind, market, _, _ = instrument_key(instrument)
                    if kind not in dataset.instrument_kinds or market not in dataset.markets:
                        raise DataFailure("unsupported")
                return provider, dataset
        raise DataFailure("unsupported")

    def _build(
        self,
        request: CollectionJobRequest,
        repo: JobRepository,
    ) -> tuple[CollectionPreview, tuple[CollectionPart, ...]]:
        self.auth.require(request.actor_id, request.scope_id, "manage")
        if len(request.instrument_ids) > self.plan_guard.max_instruments:
            # Reject before supplier metadata or repository work. This is a
            # scale guard, never an implicit truncation of dates or membership.
            raise DataFailure("invalid_input")
        provider, dataset = self._source(request)
        names = provider.names(request.instrument_ids)
        definitions: tuple[FactorDefinition, ...] = ()
        if request.dataset_kind == "factors":
            catalog = {field.factor_id: field for field in provider.factors()}
            if set(request.fields) - catalog.keys():
                raise DataFailure("unsupported")
            definitions = tuple(catalog[field] for field in sorted(request.fields))
        parts: dict[UUID, CollectionPart] = {}
        new_parts = 0
        version: list[object] = []
        for instrument in sorted(request.instrument_ids):
            for field in sorted(request.fields) if request.fields else (None,):
                prototype = CollectionPart(
                    uuid4(),
                    request.request_id,
                    request,
                    instrument,
                    field,
                    request.start,
                    request.end,
                    dataset.mapping_version,
                    names.get(instrument, "名称未核验"),
                )
                occupied = repo.coverage(prototype)
                version.append(
                    [
                        prototype.coverage_key,
                        [
                            [str(p.part_id), p.state, p.start.isoformat(), p.end.isoformat()]
                            for p in sorted(occupied, key=lambda p: str(p.part_id))
                        ],
                    ]
                )
                spans = (
                    ((request.start, request.end),)
                    if request.mode == "all"
                    else tuple(subtract(request.start, request.end, occupied))
                )
                if request.mode == "missing":
                    parts.update((part.part_id, part) for part in occupied)
                for begin, end in spans:
                    cursor = begin
                    for left, right in provider.partitions(request, begin, end):
                        if left != cursor or right <= left or right > end:
                            raise DataFailure("unverified")
                        new_parts += 1
                        if new_parts > self.plan_guard.max_new_parts:
                            raise DataFailure("invalid_input")
                        begin = left
                        part_id = uuid5(
                            request.request_id,
                            digest(
                                [
                                    prototype.coverage_key,
                                    begin.isoformat(),
                                    right.isoformat(),
                                ]
                            ),
                        )
                        parts[part_id] = CollectionPart(
                            part_id,
                            request.request_id,
                            request,
                            instrument,
                            field,
                            begin,
                            right,
                            dataset.mapping_version,
                            names.get(instrument, "名称未核验"),
                        )
                        cursor = right
                    if cursor != end:
                        raise DataFailure("unverified")
        existing = sum(p.state == "completed" for p in parts.values())
        active = sum(
            p.state in {"running", "waiting"}
            or p.job_id != request.request_id
            and p.state == "queued"
            for p in parts.values()
        )
        estimate = provider.estimate(request, len(parts) - existing - active)
        preview = CollectionPreview(
            schema_version=1,
            scope_id=request.scope_id,
            request=request,
            mapping_version=dataset.mapping_version,
            # Bind the acknowledgement to current limits as well as coverage.
            # A policy change requires a fresh preview, not an old confirmation.
            coverage_version=digest([version, self.plan_guard.model_dump(mode="json")]),
            planned_parts=len(parts),
            completed_parts=existing,
            active_parts=active,
            missing_parts=len(parts) - existing - active,
            field_definitions=definitions,
            limitations=dataset.limitations,
            instrument_names=names,
            plan_guard=self.plan_guard,
            execution_estimate=estimate,
            confirmation_required=(
                len(parts) - existing - active >= self.plan_guard.confirm_new_parts
                or estimate.credits_max >= self.plan_guard.confirm_credits
            ),
        )
        return preview, tuple(parts.values())

    def preview(self, request: CollectionJobRequest) -> CollectionPreview:
        self.auth.require(request.actor_id, request.scope_id, "manage")
        with usage_scope(request.actor_id, request.scope_id):
            return self._build(request, self.repository)[0]

    def submit(
        self,
        request: CollectionJobRequest,
        coverage_version: str,
        confirmed: bool = False,
        *,
        automatic_approval: ScheduleExecutionApproval | None = None,
    ) -> dict[str, object]:
        self.auth.require(request.actor_id, request.scope_id, "manage")
        with self.repository.atomic(request.scope_id) as repo:
            existing = repo.find(request.scope_id, request.request_id)
            if existing:
                if existing["request"] != request.model_dump(mode="json"):
                    raise DataFailure("conflict")
                return existing
            with usage_scope(request.actor_id, request.scope_id):
                preview, parts = self._build(request, repo)
            if preview.coverage_version != coverage_version:
                raise DataFailure("conflict")
            if automatic_approval is not None:
                automatic_approval.check(preview)
            if (
                preview.confirmation_required
                and confirmed is not True
                and automatic_approval is None
            ):
                raise DataFailure("invalid_input")
            return repo.submit(
                request.request_id,
                request,
                parts,
                preview.field_definitions,
                preview.instrument_names,
            )

    def read(self, actor_id: UUID, scope_id: UUID, job_id: UUID) -> dict[str, object]:
        self.auth.require(actor_id, scope_id, "read")
        return self.repository.job(scope_id, job_id)

    def qualities(
        self, actor_id: UUID, scope_id: UUID, job_id: UUID, part_id: UUID
    ) -> tuple[CollectionQualityResult, ...]:
        """Check the scoped job/part link before opening any private quality evidence."""
        self.auth.require(actor_id, scope_id, "read")
        part = self.repository.part_for_job(scope_id, job_id, part_id)
        # Validate the persistence adapter's boundary before choosing a file path.
        # The original job/actor may differ for a shared completed part; scope may not.
        if part.request.scope_id != scope_id or part.part_id != part_id:
            raise DataFailure("unavailable")
        return self.archive.qualities(part)

    def control(self, actor_id: UUID, scope_id: UUID, job_id: UUID, action: str) -> None:
        self.auth.require(actor_id, scope_id, "manage")
        if action not in {"retry", "cancel"}:
            raise DataFailure("invalid_input")
        self.repository.control(scope_id, job_id, action)

    def processing_failures(
        self, actor_id: UUID, scope_id: UUID, job_id: UUID, part_id: UUID
    ) -> tuple[CollectionProcessingFailure, ...]:
        self.auth.require(actor_id, scope_id, "read")
        part = self.repository.part_for_job(scope_id, job_id, part_id)
        if part.request.scope_id != scope_id or part.part_id != part_id:
            raise DataFailure("unavailable")
        return self.archive.processing_failures(part)

    def run_once(self) -> CollectionPart | None:
        part = self.repository.claim(datetime.now(UTC))
        if part is None:
            return None
        context_token = collection_context.set(
            {
                "correlation_id": str(part.request.correlation_id),
                "job_id": str(part.job_id),
                "part_id": str(part.part_id),
                "source": part.request.source,
                "attempt": part.attempt,
            }
        )
        usage_token = usage_identity.set(
            UsageIdentity(part.request.actor_id, part.request.scope_id)
        )
        started = monotonic()
        stage = "authorization"
        source_captured = False
        quality_rule_version = RULE_VERSION
        captures: list[CollectionRawCapture] = []
        try:
            logger.info("part_started", extra={"event": "part_started", "stage": stage})
            self.auth.require(part.request.actor_id, part.request.scope_id, "manage")
            if part.execution_kind == "normalization_replay":
                # A missing intent/handler must fail closed, including retries.
                # Offline work never enters _source or provider.fetch.
                if self.replays is None:
                    raise DataFailure("unsupported")
                stage = "offline_conversion"
                result, timezone, quality_rule_version = self.replays.execute(part)
                source_captured = True
            else:
                stage = "recovery"
                recovered = self.archive.recover(part)
                if recovered is not None:
                    self.repository.complete(part, recovered)
                    return part
                provider, dataset = self._source(part.request)
                timezone = dataset.timezone
                if dataset.mapping_version != part.mapping_version:
                    raise DataFailure("conflict")
                stage = "provider_acquisition"

                def capture(kind: RawPayloadKind, payload: object) -> None:
                    nonlocal stage, source_captured
                    stage = "raw_archive"
                    captures.append(self.archive.capture_raw(part, kind, payload))
                    if kind not in {"calendar", "http_response"}:
                        source_captured = True
                    # HTTP evidence alone cannot prove acquisition/normalization
                    # succeeded. Keep HTTP failures in the acquisition stage.
                    stage = (
                        "provider_acquisition" if kind == "http_response" else "provider_transform"
                    )

                from qtrade_data_core.normalization_input import (
                    PreparedCollectionArchive,
                    PreparedCollectionProvider,
                )

                if isinstance(provider, PreparedCollectionProvider):
                    if not isinstance(self.archive, PreparedCollectionArchive):
                        raise DataFailure("unsupported")
                    prepared_archive = self.archive
                    prepared_count = 0

                    def prepared(payload: NormalizationPayload) -> None:
                        nonlocal stage, prepared_count
                        if prepared_count:
                            raise DataFailure("unverified")
                        stage = "input_archive"
                        prepared_archive.capture_prepared(part, payload, tuple(captures))
                        prepared_count += 1
                        stage = "provider_transform"

                    result = provider.fetch_prepared(part, capture=capture, prepared=prepared)
                    if prepared_count != 1:
                        raise DataFailure("unverified")
                else:
                    # Existing third-party/controlled providers retain their original
                    # seam. They do not acquire offline replay support by implication.
                    result = provider.fetch(part, capture=capture)
            stage = "quality"
            quality = evaluate(
                part,
                result,
                timezone,
                datetime.now(UTC),
                source_captured=source_captured,
                rule_version=quality_rule_version,
            )
            self.archive.record_quality(part, quality)
            if quality.status == "blocked":
                raise DataFailure("unverified")
            if not result.records:
                self.repository.complete(part, None)
            else:
                stage = "archive"
                if part.execution_kind == "normalization_replay":
                    previous = self.archive.recover(part)
                    if previous is not None and (
                        previous.records_sha256
                        != digest([r.model_dump(mode="json") for r in result.records])
                        or previous.units != result.units
                        or previous.evidence != result.evidence
                        or previous.raw_record_count != len(result.raw)
                    ):
                        raise DataFailure("conflict")
                publication = self.archive.publish(part, result)
                self.archive.records(publication)
                self.repository.complete(part, publication)
        except UsageDenied as error:
            self.repository.wait_budget(part, error.reason, error.retry_at)
            logger.warning(
                "part_budget_wait",
                extra={"event": "part_budget_wait", "stage": stage, "error_code": "rate_limited"},
            )
        except DataFailure as error:
            failure_code = error.code
            if error.code == "unverified" and stage == "provider_transform" and source_captured:
                # A rejected conversion never produced a complete standard
                # record set. Keep its quantity unknown, and retain references
                # rather than copying supplier bodies into diagnostic records.
                refs = tuple(sorted({c.capture_id for c in captures}, key=str))
                body = {
                    "part": str(part.part_id),
                    "attempt": part.attempt,
                    "mapping": part.mapping_version,
                    "raw": [str(value) for value in refs],
                    "checker": "collection-normalization-check.v1",
                }
                try:
                    failure = CollectionProcessingFailure(
                        schema_version=1,
                        failure_id=uuid5(part.part_id, digest(body)),
                        scope_id=part.request.scope_id,
                        part_id=part.part_id,
                        job_id=part.job_id,
                        attempt=part.attempt,
                        checker_version="collection-normalization-check.v1",
                        mapping_version=part.mapping_version,
                        stage="normalization",
                        error_code="unverified",
                        status="blocked",
                        raw_capture_ids=refs,
                        failed_at=datetime.now(UTC),
                    )
                    self.archive.record_processing_failure(part, failure)
                except Exception:
                    # A missing durable check must not be presented as a saved
                    # rejection. Fail closed with a recoverable storage error.
                    failure_code = "unavailable"
                    stage = "failure_archive"
            logger.warning(
                "part_failed",
                extra={"event": "part_failed", "stage": stage, "error_code": failure_code},
            )
            retry = failure_code in {"rate_limited", "timeout", "unavailable"} and part.attempt < 8
            delay = min(3600, 30 * 2 ** min(part.attempt - 1, 6))
            self.repository.fail(
                part, failure_code, datetime.now(UTC) + timedelta(seconds=delay) if retry else None
            )
        except Exception:
            logger.warning(
                "part_failed",
                extra={"event": "part_failed", "stage": stage, "error_code": "unavailable"},
            )
            self.repository.fail(
                part,
                "unavailable",
                datetime.now(UTC) + timedelta(seconds=60) if part.attempt < 8 else None,
            )
        finally:
            logger.info(
                "part_finished",
                extra={
                    "event": "part_finished",
                    "stage": stage,
                    "duration_ms": round((monotonic() - started) * 1000),
                },
            )
            usage_identity.reset(usage_token)
            collection_context.reset(context_token)
        return part
