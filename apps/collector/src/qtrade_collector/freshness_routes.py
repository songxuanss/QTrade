"""Separate snapshot reads from manager commands that verify trusted evidence."""

import json
import logging
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Response
from pydantic import BeforeValidator

from qtrade_data_contracts.freshness import (
    FreshnessCheckRequest,
    FreshnessHistoryQuery,
    FreshnessKey,
    FreshnessResult,
)
from qtrade_data_contracts.freshness_delivery import (
    FreshnessNoticeAck,
    FreshnessNoticeClaim,
    FreshnessNoticeLease,
)
from qtrade_data_contracts.freshness_health import FreshnessHealthPage, FreshnessHealthQuery
from qtrade_data_contracts.freshness_watch import (
    FreshnessWatch,
    FreshnessWatchControl,
    FreshnessWatchCreate,
)
from qtrade_data_core.freshness_check import FreshnessCheck
from qtrade_data_core.freshness_delivery import FreshnessDelivery
from qtrade_data_core.freshness_health import FreshnessHealth
from qtrade_data_core.freshness_store import FreshnessSnapshots
from qtrade_data_core.freshness_watch import FreshnessWatches
from qtrade_data_core.ports import DataFailure


def key_wire(value: object) -> FreshnessKey:
    if isinstance(value, FreshnessKey):
        return value
    return FreshnessKey.model_validate_json(json.dumps(value))


def history_wire(value: object) -> FreshnessHistoryQuery:
    if isinstance(value, FreshnessHistoryQuery):
        return value
    return FreshnessHistoryQuery.model_validate_json(json.dumps(value))


KeyBody = Annotated[FreshnessKey, BeforeValidator(key_wire)]
HistoryBody = Annotated[FreshnessHistoryQuery, BeforeValidator(history_wire)]


def check_wire(value: object) -> FreshnessCheckRequest:
    if isinstance(value, FreshnessCheckRequest):
        return value
    return FreshnessCheckRequest.model_validate_json(json.dumps(value))


CheckBody = Annotated[FreshnessCheckRequest, BeforeValidator(check_wire)]


def ack_wire(value: object) -> FreshnessNoticeAck:
    if isinstance(value, FreshnessNoticeAck):
        return value
    return FreshnessNoticeAck.model_validate_json(json.dumps(value))


AckBody = Annotated[FreshnessNoticeAck, BeforeValidator(ack_wire)]


def watch_create_wire(value: object) -> FreshnessWatchCreate:
    if isinstance(value, FreshnessWatchCreate):
        return value
    return FreshnessWatchCreate.model_validate_json(json.dumps(value))


CreateWatchBody = Annotated[FreshnessWatchCreate, BeforeValidator(watch_create_wire)]


def health_wire(value: object) -> FreshnessHealthQuery:
    if isinstance(value, FreshnessHealthQuery):
        return value
    return FreshnessHealthQuery.model_validate_json(json.dumps(value))


HealthBody = Annotated[FreshnessHealthQuery, BeforeValidator(health_wire)]


def register_freshness_routes(
    app: FastAPI, subject: Callable[..., object], authenticated: Callable[..., object]
) -> None:
    def service(identity: tuple[UUID, UUID], key: FreshnessKey) -> FreshnessSnapshots:
        if identity[1] != key.scope_id:
            raise DataFailure("forbidden")
        return app.state.freshness_snapshots  # type: ignore[no-any-return]

    # POST carries a strict structured read selection. These are queries, not
    # commands: callers cannot submit arbitrary snapshot state.
    @app.post("/v1/freshness/latest")
    def latest(
        body: KeyBody,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> FreshnessResult:
        response.headers["Cache-Control"] = "no-store"
        return service(identity, body).latest(identity[0], body)

    @app.post("/v1/freshness/history")
    def history(
        body: HistoryBody,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> tuple[FreshnessResult, ...]:
        response.headers["Cache-Control"] = "no-store"
        return service(identity, body.key).history(
            identity[0], body.key, limit=body.limit, before=body.before
        )

    @app.post("/v1/freshness/check")
    def check(
        body: CheckBody,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> FreshnessResult:
        response.headers["Cache-Control"] = "no-store"
        checker: FreshnessCheck = app.state.freshness_check
        # The service checks current manage permission before calendar I/O and
        # again at commit. Scope and actor come only from authenticated headers.
        result = checker.check(identity[0], identity[1], body.publication_id).result
        logging.getLogger(__name__).info(
            "freshness_checked",
            extra={
                "event": "freshness_checked",
                "stage": "freshness",
                "scope_id": str(identity[1]),
                "correlation_id": str(body.publication_id),
                "state": result.status,
            },
        )
        return result

    @app.post("/v1/freshness/notices/claim", dependencies=[Depends(authenticated)])
    def claim(body: FreshnessNoticeClaim, response: Response) -> tuple[FreshnessNoticeLease, ...]:
        response.headers["Cache-Control"] = "no-store"
        delivery: FreshnessDelivery = app.state.freshness_delivery
        return delivery.claim(body)

    @app.post("/v1/freshness/notices/{notice_id}/ack")
    def acknowledge(
        notice_id: UUID,
        body: AckBody,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> dict[str, bool]:
        response.headers["Cache-Control"] = "no-store"
        delivery: FreshnessDelivery = app.state.freshness_delivery
        delivery.acknowledge(identity[0], identity[1], notice_id, body)
        return {"accepted": True}

    @app.post("/v1/freshness/watches")
    def create_watch(
        body: CreateWatchBody,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> FreshnessWatch:
        response.headers["Cache-Control"] = "no-store"
        watches: FreshnessWatches = app.state.freshness_watches
        return watches.create(identity[0], identity[1], body)

    @app.get("/v1/freshness/watches/by-publication/{publication_id}")
    def publication_watch(
        publication_id: UUID,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> FreshnessWatch:
        response.headers["Cache-Control"] = "no-store"
        watches: FreshnessWatches = app.state.freshness_watches
        return watches.by_publication(identity[0], identity[1], publication_id)

    @app.get("/v1/freshness/watches/{watch_id}")
    def get_watch(
        watch_id: UUID, response: Response, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> FreshnessWatch:
        response.headers["Cache-Control"] = "no-store"
        watches: FreshnessWatches = app.state.freshness_watches
        return watches.get(identity[0], identity[1], watch_id)

    @app.post("/v1/freshness/watches/{watch_id}/control")
    def control_watch(
        watch_id: UUID,
        body: FreshnessWatchControl,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> FreshnessWatch:
        response.headers["Cache-Control"] = "no-store"
        watches: FreshnessWatches = app.state.freshness_watches
        return watches.control(identity[0], identity[1], watch_id, body)

    @app.post("/v1/freshness/health")
    def health(
        body: HealthBody,
        response: Response,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> FreshnessHealthPage:
        if body.scope_id != identity[1]:
            raise DataFailure("forbidden")
        response.headers["Cache-Control"] = "no-store"
        health_service: FreshnessHealth = app.state.freshness_health
        return health_service.read(identity[0], body)
