"""Typed immutable dispatch decisions and schedule/request identity semantics."""

import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_collection_schedules import draft

from qtrade_data_contracts.collection import CollectionJobRequest
from qtrade_data_contracts.research_transport import ResearchPlanRequest
from qtrade_data_contracts.schedule_dispatch import (
    MarketDispatchInput,
    ResearchDispatchInput,
    ScheduleDispatchDecision,
)
from qtrade_data_contracts.schedules import ResearchScheduleSelection


def decision(value=None):
    value = value or draft()
    run = uuid4()
    now = datetime(2026, 7, 1, tzinfo=UTC)
    if value.selection.kind == "market":
        request = CollectionJobRequest(
            schema_version=1,
            scope_id=value.scope_id,
            actor_id=value.actor_id,
            request_id=run,
            correlation_id=run,
            source=value.selection.source,
            dataset_kind=value.selection.dataset_kind,
            instrument_ids=value.selection.instrument_ids,
            granularity=value.selection.granularity,
            fields=value.selection.fields,
            start=now,
            end=now + timedelta(days=1),
        )
        dispatch = MarketDispatchInput(kind="market", request=request)
    else:
        request = ResearchPlanRequest(
            schema_version=2,
            scope_id=value.scope_id,
            actor_id=value.actor_id,
            request_id=run,
            correlation_id=run,
            source=value.selection.source,
            kind=value.selection.dataset_kind,
            instrument_ids=value.selection.instrument_ids,
            date_axis=value.selection.date_axis,
            start=date(2026, 1, 1),
            end=date(2026, 7, 1),
        )
        dispatch = ResearchDispatchInput(kind="research", request=request)
    return ScheduleDispatchDecision(
        schema_version=1,
        scope_id=value.scope_id,
        run_id=run,
        schedule_id=value.schedule_id,
        revision=value.revision,
        due_at=now,
        frozen_at=now,
        dispatch=dispatch,
        policy_version="fixture.window.v1",
        basis="explicit controlled window",
    )


def test_market_and_research_inputs_roundtrip_and_bind_whole_revision():
    for value in (
        draft(),
        draft(
            selection=ResearchScheduleSelection(
                kind="research",
                source="fixture.financial",
                dataset_kind="financial_statement",
                date_axis="report_period",
                instrument_ids=("601288.SH",),
            )
        ),
    ):
        frozen = decision(value)
        assert ScheduleDispatchDecision.model_validate_json(frozen.model_dump_json()) == frozen
        frozen.check_schedule(value)
        with pytest.raises(ValueError):
            frozen.check_schedule(value.model_copy(update={"approved_credits": 1}))


@pytest.mark.parametrize(
    "changes",
    [
        {"request_id": str(uuid4())},
        {"correlation_id": str(uuid4())},
        {"scope_id": str(uuid4())},
        {"mode": "all"},
        {"licence": "fixture"},
    ],
)
def test_request_identity_and_credentials_rejected(changes):
    body = decision().model_dump(mode="json")
    body["dispatch"]["request"].update(changes)
    with pytest.raises(ValidationError):
        ScheduleDispatchDecision.model_validate_json(json.dumps(body))


def test_window_and_policy_changes_affect_content_digest():
    frozen = decision()
    assert frozen.model_copy(update={"policy_version": "fixture.window.v2"}).content_sha256 != (
        frozen.content_sha256
    )
    request = frozen.dispatch.request.model_copy(update={"end": datetime(2026, 7, 3, tzinfo=UTC)})
    changed = frozen.model_copy(
        update={"dispatch": frozen.dispatch.model_copy(update={"request": request})}
    )
    assert changed.content_sha256 != frozen.content_sha256


def test_dispatch_read_checks_current_permission_before_storage():
    from types import SimpleNamespace

    from qtrade_data_core.ports import DataFailure
    from qtrade_data_core.schedules import ScheduleControls
    from qtrade_data_http.client import StaticScopeAuthorizer

    value = draft()
    frozen = decision(value)
    reads = []
    store = SimpleNamespace(get_dispatch=lambda *args: reads.append(args) or frozen)
    auth = StaticScopeAuthorizer({})
    controls = ScheduleControls(auth, store)
    with pytest.raises(DataFailure, match="forbidden"):
        controls.get_dispatch(value.actor_id, value.scope_id, frozen.run_id)
    assert reads == []
    auth.grants[f"{value.actor_id}:{value.scope_id}"] = "read"
    assert controls.get_dispatch(value.actor_id, value.scope_id, frozen.run_id) == frozen
    assert reads == [(value.scope_id, frozen.run_id)]
