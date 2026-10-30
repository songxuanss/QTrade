"""Automatic acknowledgement semantics, separate from actual hard HTTP budgets."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_collection_schedules import draft

from qtrade_data_contracts.collection import CollectionJobRequest, CollectionPreview
from qtrade_data_contracts.planning import CollectionCostEstimate
from qtrade_data_contracts.schedules import MarketScheduleSelection
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.schedule_approval import ScheduleApprovalRequired, ScheduleExecutionApproval


def preview(value, parts=1, credits=4):
    now = datetime(2026, 7, 1, tzinfo=UTC)
    selection = value.selection
    return CollectionPreview(
        schema_version=1,
        scope_id=value.scope_id,
        request=CollectionJobRequest(
            schema_version=1,
            scope_id=value.scope_id,
            actor_id=value.actor_id,
            request_id=uuid4(),
            correlation_id=uuid4(),
            source=selection.source,
            dataset_kind=selection.dataset_kind,
            instrument_ids=selection.instrument_ids,
            granularity="1d",
            fields=selection.fields,
            start=now,
            end=now + timedelta(days=1),
        ),
        mapping_version="fixture.v1",
        coverage_version="a" * 64,
        planned_parts=parts,
        completed_parts=0,
        active_parts=0,
        missing_parts=parts,
        confirmation_required=True,
        execution_estimate=CollectionCostEstimate(
            calls_min=0,
            calls_max=credits,
            credits_min=0,
            credits_max=credits,
            accounting="internal_credit_safeguard",
            includes_retries=False,
            basis="controlled estimate",
        ),
    )


def test_exact_approved_boundary_and_zero_new_work_are_allowed():
    value = draft(approved_parts=1, approved_credits=4)
    approval = ScheduleExecutionApproval(value)
    approval.check(preview(value))
    approval.check(preview(value, parts=0, credits=0))


@pytest.mark.parametrize("parts,credits", [(2, 4), (1, 5)])
def test_excess_requires_new_approval(parts, credits):
    value = draft(approved_parts=1, approved_credits=4)
    with pytest.raises(ScheduleApprovalRequired):
        ScheduleExecutionApproval(value).check(preview(value, parts, credits))


def test_unknown_estimate_does_not_authorize_execution():
    value = draft()
    with pytest.raises(DataFailure, match="unverified"):
        ScheduleExecutionApproval(value).check(
            preview(value).model_copy(update={"execution_estimate": None})
        )


def test_factor_approval_keeps_explicit_field_selection():
    value = draft(
        selection=MarketScheduleSelection(
            kind="market",
            source="mairui.factor.history",
            dataset_kind="factors",
            granularity="1d",
            instrument_ids=("601288.SH",),
            fields=("pe",),
        )
    )
    approval = ScheduleExecutionApproval(value)
    plan = preview(value)
    approval.check(plan)
    with pytest.raises(DataFailure, match="conflict"):
        approval.check(
            plan.model_copy(
                update={"request": plan.request.model_copy(update={"fields": ("pe", "pb")})}
            )
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"actor_id": uuid4()},
        {"scope_id": uuid4()},
        {"source": "another.source"},
        {"instrument_ids": ("600000.SH",)},
        {"mode": "all"},
    ],
)
def test_approval_does_not_substitute_actor_or_allow_full_recollection(changes):
    value = draft()
    plan = preview(value)
    changed = plan.model_copy(update={"request": plan.request.model_copy(update=changes)})
    with pytest.raises(DataFailure, match="conflict"):
        ScheduleExecutionApproval(value).check(changed)
