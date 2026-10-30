"""Scoped schedule controls; policy checks precede enabling automatic work."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query
from pydantic import BeforeValidator

from qtrade_data_contracts.collection import instrument_key
from qtrade_data_contracts.schedules import (
    CollectionScheduleControl,
    CollectionScheduleDraft,
    CollectionScheduleRecord,
)
from qtrade_data_core.collection import CollectionService
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.schedule_policy import DailyPriceSchedulePolicy
from qtrade_data_core.schedule_runner import ScheduleRunner
from qtrade_data_core.usage_execution import usage_scope


def draft_wire(value: object) -> CollectionScheduleDraft:
    if isinstance(value, CollectionScheduleDraft):
        return value
    return CollectionScheduleDraft.model_validate_json(json.dumps(value))


DraftBody = Annotated[CollectionScheduleDraft, BeforeValidator(draft_wire)]


def register_schedule_routes(app: FastAPI, subject: Callable[..., object]) -> None:
    def runner() -> ScheduleRunner:
        return app.state.schedule_runner  # type: ignore[no-any-return]

    def validate(draft: CollectionScheduleDraft) -> None:
        service: CollectionService = app.state.collection_service
        if len(draft.selection.instrument_ids) > service.plan_guard.max_instruments:
            raise DataFailure("invalid_input")
        dataset = next((d for d in service.catalog() if d.source == draft.selection.source), None)
        if dataset is None or dataset.dataset_kind != "bars" or "1d" not in dataset.periods:
            raise DataFailure("unsupported")
        if not dataset.configured:
            raise DataFailure("unavailable")
        for identifier in draft.selection.instrument_ids:
            kind, market, _, _ = instrument_key(identifier)
            if kind not in dataset.instrument_kinds or market not in dataset.markets:
                raise DataFailure("unsupported")
        policy = runner().policy
        if not isinstance(policy, DailyPriceSchedulePolicy):
            raise DataFailure("unsupported")
        with usage_scope(draft.actor_id, draft.scope_id):
            policy.validate(draft, datetime.now(UTC))

    @app.get("/v1/schedules")
    def listing(
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    ) -> tuple[CollectionScheduleRecord, ...]:
        return runner().controls.list(*identity, limit=limit, offset=offset)

    @app.post("/v1/schedules", status_code=201)
    def create(
        body: DraftBody, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> CollectionScheduleRecord:
        if (body.actor_id, body.scope_id) != identity:
            raise DataFailure("forbidden")
        runner().auth.require(*identity, "manage")
        validate(body)
        return runner().controls.create(body, datetime.now(UTC))

    @app.get("/v1/schedules/{schedule}")
    def read(
        schedule: UUID, identity: Annotated[tuple[UUID, UUID], Depends(subject)]
    ) -> CollectionScheduleRecord:
        return runner().controls.get(*identity, schedule)

    @app.post("/v1/schedules/{schedule}/{action}")
    def control(
        schedule: UUID,
        action: str,
        body: CollectionScheduleControl,
        identity: Annotated[tuple[UUID, UUID], Depends(subject)],
    ) -> CollectionScheduleRecord:
        if action == "disable":
            return runner().controls.disable(*identity, schedule, body.revision)
        if action != "enable":
            raise DataFailure("invalid_input")
        runner().auth.require(*identity, "manage")
        record = runner().controls.get(*identity, schedule)
        runner().auth.require(record.draft.actor_id, record.scope_id, "manage")
        if record.revision != body.revision:
            raise DataFailure("conflict")
        validate(record.draft)
        return runner().controls.enable(*identity, schedule, body.revision)
