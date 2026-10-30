"""Independent authenticated API and leased background consumer."""

import asyncio
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool

from qtrade_data_contracts.collection import (
    CollectionDefaultRange,
    CollectionPeriod,
    CollectionRecordPage,
    CollectionWireRequest,
    digest,
)
from qtrade_data_contracts.collection_quality import CollectionQualityResult
from qtrade_data_contracts.factor_catalog import FactorCatalogSnapshot
from qtrade_data_contracts.planning import CollectionPlanGuard
from qtrade_data_contracts.processing_failure import CollectionProcessingFailure
from qtrade_data_contracts.source_profiles import SourceProfileCatalog
from qtrade_data_contracts.usage import UsageSnapshot
from qtrade_data_core.collection import CollectionService
from qtrade_data_core.collection_replay import CollectionReplayService
from qtrade_data_core.freshness_check import FreshnessCheck
from qtrade_data_core.freshness_delivery import FreshnessDelivery
from qtrade_data_core.freshness_evidence import DailyPublicationEvidence
from qtrade_data_core.freshness_health import FreshnessHealth
from qtrade_data_core.freshness_refresh import FreshnessRefresh
from qtrade_data_core.freshness_store import FreshnessSnapshots
from qtrade_data_core.freshness_watch import FreshnessWatches, FreshnessWatchRunner
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.research import ResearchService
from qtrade_data_core.research_acquisition import ResearchAcquisitionEvidence
from qtrade_data_core.research_collection import ResearchCollectionExecutor
from qtrade_data_core.research_jobs import ResearchJobsService
from qtrade_data_core.research_release import ResearchReleaseService
from qtrade_data_core.research_replay import ResearchReplayService
from qtrade_data_core.research_worker import ResearchWorker
from qtrade_data_core.schedule_policy import DailyPriceSchedulePolicy
from qtrade_data_core.schedule_runner import ScheduleRunner
from qtrade_data_core.usage import UsagePolicy
from qtrade_data_core.usage_execution import UsageGate, usage_scope
from qtrade_data_files.archive import FileCollectionArchive
from qtrade_data_files.research import FileResearchArchive
from qtrade_data_http.client import RemoteDataAuthorizer, StaticScopeAuthorizer
from qtrade_data_mairui.collection import MaiRuiCollection
from qtrade_data_mairui.research import MaiRuiDistributions, MaiRuiFinancial
from qtrade_data_mairui.source_profiles import profiles as mairui_source_profiles
from qtrade_data_postgres.collection_replay import PostgresCollectionReplays
from qtrade_data_postgres.freshness import PostgresFreshness
from qtrade_data_postgres.freshness_delivery import PostgresFreshnessDelivery
from qtrade_data_postgres.freshness_health import PostgresFreshnessHealth
from qtrade_data_postgres.freshness_watch import PostgresFreshnessWatches, WatchedFreshness
from qtrade_data_postgres.migrate import apply
from qtrade_data_postgres.research_acquisition import PostgresResearchResponses
from qtrade_data_postgres.research_publications import (
    PostgresResearchPublications,
    PostgresResearchReader,
)
from qtrade_data_postgres.research_replay import PostgresResearchReplays
from qtrade_data_postgres.scheduled_jobs import PostgresScheduledJobs
from qtrade_data_postgres.schedules import PostgresSchedules
from qtrade_data_postgres.usage import PostgresUsageLedger

from .collection_replay_routes import limits as collection_replay_limits
from .collection_replay_routes import register_collection_replay_routes
from .config import configuration
from .diagnostics import configure_logging
from .freshness_routes import register_freshness_routes
from .replay_routes import register_replay_routes, replay_limits
from .research_routes import register_research_routes
from .schedule_routes import register_schedule_routes
from .usage_config import scope_limits


class SubmitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: CollectionWireRequest
    coverage_version: str
    confirmed: StrictBool = False


def create_app() -> FastAPI:
    config = configuration()
    token = config["COLLECTOR_SERVICE_TOKEN"]
    if len(token) < 32:
        raise ValueError("strong collection service token required")
    configure_logging(config.get("COLLECTOR_LOG_LEVEL", "INFO"))
    logger = logging.getLogger(__name__)
    repository = PostgresCollectionReplays(config["DATA_COLLECTION_DATABASE_URL"])
    archive = FileCollectionArchive(Path(config.get("COLLECTOR_ARCHIVE_ROOT", "/data/archives")))
    allow_http = config.get("COLLECTOR_ALLOW_HTTP") == "true"
    auth = (
        RemoteDataAuthorizer(config["COLLECTOR_AUTHORIZER_URL"], token, allow_http=allow_http)
        if config.get("COLLECTOR_AUTHORIZER_URL")
        else StaticScopeAuthorizer(json.loads(config.get("COLLECTOR_SCOPE_GRANTS_JSON") or "{}"))
    )
    interval = float(config.get("COLLECTOR_SOURCE_INTERVAL_SECONDS", "3"))
    if not 0 <= interval <= 60:
        raise ValueError("invalid provider pacing")
    usage_ledger = PostgresUsageLedger(config["DATA_COLLECTION_DATABASE_URL"])
    usage = UsageGate(
        usage_ledger,
        "mairui",
        UsagePolicy(
            int(config.get("COLLECTOR_MAIRUI_DAILY_CREDITS", "10000")),
            int(config.get("COLLECTOR_SCOPE_DAILY_CREDITS", "2000")),
            int(config.get("COLLECTOR_SOURCE_CONCURRENCY", "4")),
            int(config.get("COLLECTOR_SCOPE_CONCURRENCY", "2")),
            lease_seconds=3600,
        ),
        scope_limits(config.get("COLLECTOR_SCOPE_LIMITS_JSON", "")),
    )
    provider = MaiRuiCollection(
        config.get("MAIRUI_LICENCE"),
        pro=config.get("COLLECTOR_MAIRUI_PRO_ENABLED") == "true",
        cache=Path(config.get("COLLECTOR_ARCHIVE_ROOT", "/data/archives")) / "provider-cache",
        pace=lambda: repository.pace("mairui", interval),
        usage=usage,
        request_timeout_seconds=float(config.get("COLLECTOR_REQUEST_TIMEOUT_SECONDS", "90")),
        partition_max_days=int(config.get("COLLECTOR_MAIRUI_PARTITION_MAX_DAYS", "0")),
    )
    service = CollectionService(
        auth,
        repository,
        archive,
        (provider,),
        CollectionPlanGuard(
            version="collection-plan-guard.v1",
            max_instruments=int(config.get("COLLECTOR_PLAN_MAX_INSTRUMENTS", "10000")),
            max_new_parts=int(config.get("COLLECTOR_PLAN_MAX_NEW_PARTS", "20000")),
            confirm_new_parts=int(config.get("COLLECTOR_PLAN_CONFIRM_PARTS", "100")),
            confirm_credits=int(config.get("COLLECTOR_PLAN_CONFIRM_CREDITS", "500")),
        ),
    )
    schedules = PostgresSchedules(config["DATA_COLLECTION_DATABASE_URL"])
    scheduler = ScheduleRunner(
        auth,
        schedules,
        DailyPriceSchedulePolicy(provider),
        lambda lease, run: CollectionService(
            auth,
            PostgresScheduledJobs(config["DATA_COLLECTION_DATABASE_URL"], lease, run),
            archive,
            (provider,),
            service.plan_guard,
        ),
    )
    scheduler_interval = float(config.get("COLLECTOR_SCHEDULER_INTERVAL_SECONDS", "2"))
    if not 1 <= scheduler_interval <= 60:
        raise ValueError("invalid scheduler interval")
    freshness_enabled = config.get("COLLECTOR_RUN_FRESHNESS", "true")
    if freshness_enabled not in {"true", "false"}:
        raise ValueError("invalid freshness worker flag")

    async def dispatch_schedules() -> None:
        # Separate task: scheduled metadata must not stall normal Worker/UI.
        while True:
            try:
                tick = await asyncio.to_thread(scheduler.run_once)
                if tick:
                    logger.info(
                        "schedule_tick",
                        extra={
                            "event": "schedule_tick",
                            "stage": "scheduler",
                            "state": tick.state,
                            "scope_id": str(tick.scope_id),
                            "schedule_id": str(tick.schedule_id),
                            "job_id": str(tick.run_id) if tick.run_id else None,
                            "error_code": tick.error_code,
                        },
                    )
            except Exception:
                logger.error(
                    "scheduler_deferred",
                    extra={
                        "event": "scheduler_deferred",
                        "stage": "scheduler",
                        "error_code": "unavailable",
                    },
                )
            app.state.scheduler_last_cycle = monotonic()
            await asyncio.sleep(scheduler_interval)

    async def consume() -> None:
        while True:
            try:
                part = await asyncio.to_thread(service.run_once)
                await asyncio.to_thread(research_worker.run_once)
                if part:
                    state = repository.job(part.request.scope_id, part.job_id)
                    logger.info(
                        "part_processed",
                        extra={
                            "event": "part_processed",
                            "part_id": str(part.part_id),
                            "job_id": str(part.job_id),
                            "correlation_id": str(part.request.correlation_id),
                            "source": part.request.source,
                            "attempt": part.attempt,
                            "stage": "worker",
                            "state": state["state"],
                        },
                    )
            except Exception:
                logger.error(
                    "worker_deferred",
                    extra={
                        "event": "worker_deferred",
                        "stage": "worker",
                        "error_code": "unavailable",
                    },
                )
            app.state.worker_last_cycle = monotonic()
            await asyncio.sleep(0.5)

    async def refresh_metadata() -> None:
        # Metadata refresh has its own task: neither an HTTP reader nor a queued
        # collection part has to wait for an unrelated supplier catalog request.
        while True:
            if provider.licence and provider.pro:
                try:
                    await asyncio.to_thread(provider.factors)
                except DataFailure as error:
                    logger.warning(
                        "metadata_refresh_failed",
                        extra={
                            "event": "metadata_refresh_failed",
                            "stage": "metadata",
                            "source": "mairui",
                            "error_code": error.code,
                        },
                    )
                except Exception:
                    logger.warning(
                        "metadata_refresh_failed",
                        extra={
                            "event": "metadata_refresh_failed",
                            "stage": "metadata",
                            "source": "mairui",
                            "error_code": "unavailable",
                        },
                    )
            await asyncio.sleep(60)

    async def check_freshness_watches() -> None:
        while True:
            try:
                lease = await asyncio.to_thread(app.state.freshness_watch_runner.run_once)
                if lease:
                    logger.info(
                        "freshness_watch_tick",
                        extra={
                            "event": "freshness_watch_tick",
                            "stage": "freshness",
                            "scope_id": str(lease.watch.scope_id),
                            "correlation_id": str(lease.watch.watch_id),
                        },
                    )
            except Exception:
                logger.warning(
                    "freshness_watch_deferred",
                    extra={
                        "event": "freshness_watch_deferred",
                        "stage": "freshness",
                        "error_code": "unavailable",
                    },
                )
            await asyncio.sleep(5)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        await asyncio.to_thread(apply, config["DATA_COLLECTION_DATABASE_URL"])
        app.state.worker_last_cycle = monotonic()
        app.state.scheduler_last_cycle = monotonic()
        schedule_task = (
            asyncio.create_task(dispatch_schedules())
            if config.get("COLLECTOR_RUN_SCHEDULER", "true") == "true"
            else None
        )
        worker = (
            asyncio.create_task(consume())
            if config.get("COLLECTOR_RUN_WORKER", "true") == "true"
            else None
        )
        metadata = asyncio.create_task(refresh_metadata())
        freshness_task = (
            asyncio.create_task(check_freshness_watches()) if freshness_enabled == "true" else None
        )
        logger.info("started", extra={"event": "started", "stage": "startup"})
        yield
        if freshness_task:
            freshness_task.cancel()
            try:
                await freshness_task
            except asyncio.CancelledError:
                pass
        if schedule_task:
            schedule_task.cancel()
            try:
                await schedule_task
            except asyncio.CancelledError:
                pass
        metadata.cancel()
        try:
            await metadata
        except asyncio.CancelledError:
            pass
        if worker:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Independent Data Collector", version="1.0", lifespan=lifespan)
    app.state.collection_service = service
    app.state.collection_replays = CollectionReplayService(
        auth, archive, repository, (provider,), limits=collection_replay_limits(config)
    )
    service.replays = app.state.collection_replays
    app.state.schedule_runner = scheduler
    app.state.usage_ledger = usage_ledger
    research_archive = FileResearchArchive(
        Path(config.get("COLLECTOR_ARCHIVE_ROOT", "/data/archives"))
    )
    research_repository = PostgresResearchReplays(config["DATA_COLLECTION_DATABASE_URL"])
    research_providers = (
        MaiRuiFinancial(provider, "balance"),
        MaiRuiFinancial(provider, "income"),
        MaiRuiFinancial(provider, "cashflow"),
        MaiRuiDistributions(provider),
    )
    app.state.research_service = ResearchService(
        auth,
        research_providers,
        PostgresResearchReader(
            PostgresResearchPublications(config["DATA_COLLECTION_DATABASE_URL"]), research_archive
        ),
        plan_guard=service.plan_guard,
    )

    app.state.research_jobs = ResearchJobsService(
        auth,
        app.state.research_service,
        research_repository,
        responses=PostgresResearchResponses(config["DATA_COLLECTION_DATABASE_URL"]),
    )
    # One repository owns both ordinary and offline queue admission. Replay
    # intents are committed atomically before this shared Worker can claim them.
    app.state.research_replays = ResearchReplayService(
        auth,
        research_archive,
        research_repository,
        research_providers,
        limits=replay_limits(config),
    )
    research_worker = ResearchWorker(
        research_repository,
        ResearchCollectionExecutor(
            auth,
            research_archive,
            research_repository,
            acquisition=ResearchAcquisitionEvidence(
                research_archive, PostgresResearchResponses(config["DATA_COLLECTION_DATABASE_URL"])
            ),
        ),
        ResearchReleaseService(auth, research_archive, research_repository),
        research_providers,
        replays=app.state.research_replays,
    )

    @app.exception_handler(DataFailure)
    async def data_failure(request: Request, error: DataFailure) -> JSONResponse:
        status = {
            "invalid_input": 400,
            "unsupported": 400,
            "unverified": 400,
            "unauthorized": 401,
            "forbidden": 403,
            "not_found": 404,
            "conflict": 409,
            "rate_limited": 429,
        }.get(error.code, 503)
        logger.warning(
            "request_rejected",
            extra={"event": "request_rejected", "stage": "api", "error_code": error.code},
        )
        return JSONResponse(
            status_code=status,
            content={"detail": {"code": error.code}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": {"code": "invalid_input"}})

    def authenticated(authorization: Annotated[str | None, Header()] = None) -> None:
        if not hmac.compare_digest(authorization or "", "Bearer " + token):
            raise DataFailure("unauthorized")

    async def subject(
        _: Annotated[None, Depends(authenticated)],
        actor: Annotated[UUID, Header(alias="X-Data-Actor")],
        scope: Annotated[UUID, Header(alias="X-Data-Scope")],
    ) -> AsyncIterator[tuple[UUID, UUID]]:
        await asyncio.to_thread(auth.require, actor, scope, "read")
        # An async yielding dependency sets context before Starlette copies it
        # into the endpoint thread; reset occurs in the same request task.
        with usage_scope(actor, scope):
            yield actor, scope

    freshness_repository = PostgresFreshness(config["DATA_COLLECTION_DATABASE_URL"])
    app.state.freshness_snapshots = FreshnessSnapshots(auth, freshness_repository)
    app.state.freshness_check = FreshnessCheck(
        auth,
        repository,
        DailyPublicationEvidence(auth, repository, archive, replays=app.state.collection_replays),
        provider,
        FreshnessRefresh(auth, freshness_repository),
    )
    watch_repository = PostgresFreshnessWatches(config["DATA_COLLECTION_DATABASE_URL"])
    app.state.freshness_watches = FreshnessWatches(
        auth, watch_repository, app.state.freshness_check
    )
    app.state.freshness_watch_runner = FreshnessWatchRunner(
        auth,
        watch_repository,
        lambda lease: FreshnessCheck(
            auth,
            repository,
            DailyPublicationEvidence(
                auth, repository, archive, replays=app.state.collection_replays
            ),
            provider,
            FreshnessRefresh(auth, WatchedFreshness(watch_repository, lease)),
        ),
    )
    app.state.freshness_delivery = FreshnessDelivery(
        auth,
        PostgresFreshnessDelivery(config["DATA_COLLECTION_DATABASE_URL"]),
        config.get("COLLECTOR_FRESHNESS_CONSUMER_ID", "qtrade-alerts"),
    )
    register_freshness_routes(app, subject, authenticated)
    app.state.freshness_health = FreshnessHealth(
        auth,
        PostgresFreshnessHealth(config["DATA_COLLECTION_DATABASE_URL"]),
        service.catalog,
        config.get("COLLECTOR_FRESHNESS_CONSUMER_ID", "qtrade-alerts"),
        int(config.get("COLLECTOR_FRESHNESS_HEALTH_MAX_AGE_SECONDS", "900")),
    )
    register_schedule_routes(app, subject)

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"service": "data-collector", "schema_version": 1}

    @app.get("/ready")
    def ready() -> dict[str, bool]:
        # Uses only the collector database; callback outages block work, not startup.
        with repository._connection() as conn:
            conn.execute("SELECT 1")
        if (
            config.get("COLLECTOR_RUN_WORKER", "true") == "true"
            and monotonic() - app.state.worker_last_cycle > 180
        ):
            raise HTTPException(503, "worker cycle delayed")
        return {"ready": True}

    @app.get("/v1/catalog")
    def catalog(identity: Annotated[tuple[UUID, UUID], Depends(subject)]) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in service.catalog()]

    @app.get("/v1/source-profiles", response_model=SourceProfileCatalog)
    def source_profiles(
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> SourceProfileCatalog:
        # Editorial evidence is read independently from immutable plan objects.
        # Local configuration cannot establish live permission or completeness.
        response.headers["Cache-Control"] = "no-store"
        return SourceProfileCatalog(
            schema_version=1, scope_id=identity[1], profiles=mairui_source_profiles(provider)
        )

    @app.get("/v1/factors")
    def factors(identity: Annotated[tuple[UUID, UUID], Depends(subject)]) -> list[dict[str, Any]]:
        snapshot = provider.factor_catalog_snapshot()
        if not snapshot.definitions:
            raise DataFailure(
                "unauthorized" if snapshot.status == "unconfigured" else "unavailable"
            )
        return [item.model_dump(mode="json") for item in snapshot.definitions]

    @app.get("/v1/factor-catalog", response_model=FactorCatalogSnapshot)
    def factor_catalog(
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> FactorCatalogSnapshot:
        return provider.factor_catalog_snapshot()

    @app.get("/v1/usage", response_model=UsageSnapshot)
    def usage_snapshot(
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> UsageSnapshot:
        # Scope is derived exclusively from the current authorized dependency;
        # callers cannot supply a different scope/source or request raw tokens.
        return usage_ledger.snapshot(
            usage.source, identity[1], usage.policy_for(identity[1]), datetime.now(UTC)
        )

    @app.get("/v1/default-range", response_model=CollectionDefaultRange)
    def default_range(
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
        source: Annotated[str, Query(min_length=1, max_length=100)],
        granularity: CollectionPeriod,
    ) -> CollectionDefaultRange:
        result = provider.default_range(source, granularity, datetime.now(UTC))
        return CollectionDefaultRange(source=source, granularity=granularity, **asdict(result))

    @app.get("/v1/names")
    def names(
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
        instrument_id: Annotated[list[str], Query(max_length=200)],
    ) -> dict[str, str]:
        try:
            return provider.names(tuple(instrument_id))
        except ValueError:
            raise DataFailure("invalid_input") from None

    @app.post("/v1/preview")
    def preview(
        body: CollectionWireRequest, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> dict[str, Any]:
        if (body.actor_id, body.scope_id) != identity:
            raise DataFailure("forbidden")
        result = service.preview(body)
        logger.info(
            "preview",
            extra={
                "event": "preview",
                "correlation_id": str(body.correlation_id),
                "job_id": str(body.request_id),
                "source": body.source,
                "stage": "plan",
            },
        )
        return result.model_dump(mode="json")

    @app.post("/v1/jobs", status_code=201)
    def submit(
        body: SubmitBody, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> dict[str, object]:
        if (body.request.actor_id, body.request.scope_id) != identity:
            raise DataFailure("forbidden")
        result = service.submit(body.request, body.coverage_version, body.confirmed)
        logger.info(
            "submitted",
            extra={
                "event": "submitted",
                "correlation_id": str(body.request.correlation_id),
                "job_id": str(body.request.request_id),
                "source": body.request.source,
                "stage": "submit",
            },
        )
        return result

    @app.get("/v1/jobs")
    def jobs(identity: Annotated[tuple[UUID, UUID], Depends(subject)]) -> list[dict[str, object]]:
        return repository.jobs(identity[1])

    @app.get("/v1/jobs/{job_id}")
    def job(
        job_id: UUID,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        return repository.job(identity[1], job_id, offset)

    @app.get(
        "/v1/jobs/{job_id}/parts/{part_id}/quality", response_model=list[CollectionQualityResult]
    )
    def qualities(
        job_id: UUID,
        part_id: UUID,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> list[CollectionQualityResult]:
        return list(service.qualities(*identity, job_id, part_id))

    @app.get(
        "/v1/jobs/{job_id}/parts/{part_id}/processing-failures",
        response_model=list[CollectionProcessingFailure],
    )
    def processing_failures(
        job_id: UUID,
        part_id: UUID,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> list[CollectionProcessingFailure]:
        return list(service.processing_failures(*identity, job_id, part_id))

    @app.post("/v1/jobs/{job_id}/{action}")
    def control(
        job_id: UUID, action: str, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> dict[str, bool]:
        service.control(*identity, job_id, action)
        return {"accepted": True}

    @app.get("/v1/publications/{publication_id}")
    def publication(
        publication_id: UUID, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> dict[str, Any]:
        return repository.publication(identity[1], publication_id).model_dump(mode="json")

    @app.get("/v1/publications/{publication_id}/records")
    def records(
        publication_id: UUID,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    ) -> dict[str, Any]:
        publication = repository.publication(identity[1], publication_id)
        values = archive.records(publication)
        page = values[offset : offset + limit]
        return CollectionRecordPage(
            schema_version=1,
            scope_id=identity[1],
            publication_id=publication_id,
            records=page,
            offset=offset,
            next_offset=offset + len(page) if offset + len(page) < len(values) else None,
            page_sha256=digest([item.model_dump(mode="json") for item in page]),
        ).model_dump(mode="json")

    @app.get("/v1/deliveries", dependencies=[Depends(authenticated)])
    def deliveries(
        consumer_id: Annotated[str, Query(pattern=r"^[a-zA-Z0-9_-]{1,64}$")],
    ) -> list[dict[str, object]]:
        return repository.deliveries(consumer_id)

    @app.post("/v1/deliveries/{publication_id}/ack")
    def acknowledge(
        publication_id: UUID,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
        consumer_id: Annotated[str, Query(pattern=r"^[a-zA-Z0-9_-]{1,64}$")],
    ) -> dict[str, bool]:
        auth.require(*identity, "manage")
        repository.acknowledge(identity[1], publication_id, consumer_id)
        return {"accepted": True}

    register_collection_replay_routes(app, subject)
    register_research_routes(app, subject)
    register_replay_routes(app, subject)
    return app


app = create_app()
