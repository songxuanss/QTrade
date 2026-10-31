"""Durable historical collection task consumer and process health endpoints."""

import asyncio
import logging
import os
from datetime import UTC, datetime
from math import ceil
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI

from qtrade_application.alert_evaluation import AlertEvaluationService
from qtrade_application.alerts import AlertEventService, AlertRuleService
from qtrade_application.ingestion import HistoricalIngestionWorker
from qtrade_composition.collection_gateway import collection_gateway_from_env
from qtrade_composition.collector import CollectorImporter, collector_client_from_env
from qtrade_composition.collector_freshness import CollectorFreshnessConsumer
from qtrade_composition.ingestion import historical_ingestion_from_env
from qtrade_composition.notifications import notification_composition_from_env
from qtrade_composition.startup import validate_startup
from qtrade_domain.authorization import Permission
from qtrade_domain.notification_delivery import NotificationAttempt
from qtrade_domain.ports import ErrorCode, PortError
from qtrade_postgres.alert_evaluation import PostgresAlertEvaluationStore
from qtrade_postgres.alert_events import PostgresAlertEventStore
from qtrade_postgres.alert_jobs import PostgresAlertEvaluationQueue
from qtrade_postgres.alerts import PostgresAlertRuleStore
from qtrade_postgres.ingestion import PostgresIngestionRepository
from qtrade_postgres.notification_delivery import PostgresNotificationDeliveryQueue
from qtrade_postgres.workspaces import PostgresWorkspaceStore
from qtrade_runtime.health import create_health_app
from qtrade_worker.backtests import consume_backtests

validate_startup()
app: FastAPI = create_health_app("qtrade-worker")
logger = logging.getLogger(__name__)


async def _consume(ingestion: HistoricalIngestionWorker) -> None:
    repository = PostgresIngestionRepository(os.environ["DATABASE_URL"])
    workspaces = PostgresWorkspaceStore(os.environ["DATABASE_URL"])
    worker_id = uuid4()
    while True:
        now = datetime.now(UTC)
        try:
            tasks = await asyncio.to_thread(repository.ready_tasks, now, 100)
            for task in tasks:
                access = await asyncio.to_thread(
                    workspaces.access, task.actor_id, task.request.workspace_id
                )
                if access is None:
                    continue
                with app.state.telemetry.operation("collection.consume", task.request.task_id):
                    await asyncio.to_thread(
                        ingestion.run_once,
                        access,
                        task.request.task_id,
                        worker_id,
                        datetime.now(UTC),
                    )
        except Exception:
            # Durable task state remains queued/waiting and will be reconsidered next cycle.
            logger.exception("historical collection worker cycle failed")
        await asyncio.sleep(1)


async def _consume_campaigns() -> None:
    gateway = collection_gateway_from_env()
    if gateway is None:
        return
    while True:
        try:
            campaigns = await asyncio.to_thread(lambda: tuple(gateway.ready()))
            for scope_id, campaign_id in campaigns:
                try:
                    await asyncio.to_thread(gateway.process, scope_id, campaign_id)
                except Exception:
                    # Log only stable identifiers; credentials/upstream errors never enter logs.
                    logger.warning("campaign processing deferred: %s", campaign_id)
        except Exception:
            logger.warning("campaign inventory temporarily unavailable")
        await asyncio.sleep(1)


async def _consume_collector_publications() -> None:
    client = collector_client_from_env()
    if client is None:
        return
    importer = CollectorImporter(client, os.environ)
    while True:
        try:
            await asyncio.to_thread(importer.consume)
        except Exception:
            logger.warning("collector publication inventory temporarily unavailable")
        await asyncio.sleep(3)


async def _consume_collector_freshness() -> None:
    client = collector_client_from_env()
    if client is None:
        return
    consumer = CollectorFreshnessConsumer(client, os.environ)
    while True:
        try:
            await asyncio.to_thread(consumer.consume)
        except Exception:
            logger.warning("collector freshness inventory temporarily unavailable")
        await asyncio.sleep(3)


async def _consume_alert_evaluations() -> None:
    database_url = os.environ["DATABASE_URL"]
    queue = PostgresAlertEvaluationQueue(database_url)
    notification_queue = PostgresNotificationDeliveryQueue(database_url)
    rules = AlertRuleService(PostgresAlertRuleStore(database_url))
    events = AlertEventService(PostgresAlertEventStore(database_url))
    evaluator = AlertEvaluationService(
        rules,
        events,
        PostgresAlertEvaluationStore(database_url),
        notification_queue=notification_queue,
    )
    workspaces = PostgresWorkspaceStore(database_url)
    while True:
        now = datetime.now(UTC)
        try:
            leases = await asyncio.to_thread(queue.ready, now, 100, 30)
            for lease in leases:
                try:
                    access = await asyncio.to_thread(
                        workspaces.access, lease.job.actor_id, lease.job.workspace_id
                    )
                    if access is None:
                        await asyncio.to_thread(queue.finish, lease, now, succeeded=True)
                        continue
                    access.require(Permission.ALERT_RULE_WRITE)
                    with app.state.telemetry.operation("alert.evaluate", lease.job.job_id):
                        await asyncio.to_thread(evaluator.evaluate_job, lease.job)
                    await asyncio.to_thread(queue.finish, lease, now, succeeded=True)
                except PortError as error:
                    if error.code in {ErrorCode.REJECTED, ErrorCode.NOT_FOUND}:
                        await asyncio.to_thread(queue.finish, lease, now, succeeded=True)
                    else:
                        logger.exception("alert evaluation job failed")
                        await asyncio.to_thread(queue.finish, lease, now, succeeded=False)
                except Exception:
                    logger.exception("alert evaluation job failed")
                    await asyncio.to_thread(queue.finish, lease, now, succeeded=False)
        except Exception:
            logger.exception("alert evaluation worker cycle failed")
        await asyncio.sleep(0.5)


async def _consume_notifications() -> None:
    queue, dispatcher = notification_composition_from_env()
    while True:
        now = datetime.now(UTC)
        try:
            leases = await asyncio.to_thread(queue.ready, now, 100, 30)
            for lease in leases:
                retry_after: int | None = None
                try:
                    with app.state.telemetry.operation(
                        "notification.deliver", lease.notification.notification_id
                    ):
                        receipt = await asyncio.to_thread(dispatcher.send, lease.notification)
                    status: Literal["delivered", "retrying", "failed"] = "delivered"
                    summary = f"{receipt.status}:{receipt.provider_reference}"
                except PortError as error:
                    retry_after = (
                        max(1, min(3600, ceil(error.retry_after or 30)))
                        if error.retryable
                        else None
                    )
                    status = "retrying" if retry_after is not None else "failed"
                    summary = error.code.value
                except Exception:
                    retry_after = 30
                    status = "retrying"
                    summary = "adapter_error"
                attempt = NotificationAttempt(
                    lease.notification.workspace_id,
                    lease.notification.notification_id,
                    lease.notification.channel,
                    lease.notification.destination_id,
                    status,
                    summary[:512],
                    datetime.now(UTC),
                    lease.attempt,
                )
                await asyncio.to_thread(
                    queue.finish,
                    lease,
                    attempt,
                    retry_after_seconds=retry_after,
                )
        except Exception:
            logger.error("notification delivery worker cycle failed")
        await asyncio.sleep(0.5)


@app.on_event("startup")
async def start_consumer() -> None:
    consumers = [
        asyncio.create_task(_consume_collector_publications()),
        asyncio.create_task(_consume_collector_freshness()),
        asyncio.create_task(_consume_campaigns()),
        asyncio.create_task(_consume_alert_evaluations()),
        asyncio.create_task(_consume_notifications()),
        asyncio.create_task(consume_backtests()),
    ]
    ingestion = historical_ingestion_from_env()
    if ingestion is not None:
        consumers.append(asyncio.create_task(_consume(ingestion)))
    app.state.consumers = consumers


@app.on_event("shutdown")
async def stop_consumer() -> None:
    consumers = getattr(app.state, "consumers", [])
    for task in consumers:
        task.cancel()
    for task in consumers:
        try:
            await task
        except asyncio.CancelledError:
            pass
