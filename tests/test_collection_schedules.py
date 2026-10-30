"""Frozen scheduler inputs, permission controls and credential rejection."""

import json
from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
from pydantic import ValidationError

from qtrade_data_contracts.schedules import CollectionScheduleDraft, MarketScheduleSelection


def draft(**changes):
    fields = dict(
        schema_version=1,
        scope_id=uuid4(),
        schedule_id=uuid4(),
        actor_id=uuid4(),
        name="日频补缺",
        selection=MarketScheduleSelection(
            kind="market",
            source="mairui.history.bars",
            instrument_ids=("601288.SH",),
            dataset_kind="bars",
            granularity="1d",
        ),
        cadence="daily",
        timezone="Asia/Shanghai",
        local_time=time(17, 15),
        approved_parts=100,
        approved_credits=500,
        automation_confirmed=True,
    )
    return CollectionScheduleDraft(**(fields | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"automation_confirmed": False},
        {"automation_confirmed": 1},
        {"catchup_days": 0},
        {"approved_credits": True},
        {"local_time": "17:15"},
        {"local_time": time(17, 15, 1)},
        {"timezone": "unknown/market"},
    ],
)
def test_schedule_requires_explicit_bounded_inputs(changes):
    with pytest.raises(ValidationError):
        draft(**changes)


def test_frozen_revision_changes_and_credentials_rejected():
    value = draft()
    assert CollectionScheduleDraft.model_validate_json(value.model_dump_json()) == value
    body = value.model_dump(mode="json")
    changed = CollectionScheduleDraft.model_validate_json(
        json.dumps(body | {"approved_credits": 250})
    )
    assert changed.revision != value.revision
    for change in ({"token": "fixture"}, {"licence": "fixture"}, {"password": "fixture"}):
        with pytest.raises(ValidationError):
            CollectionScheduleDraft.model_validate_json(json.dumps(body | change))


def test_permission_checked_before_any_storage():
    from types import SimpleNamespace

    from qtrade_data_core.ports import DataFailure
    from qtrade_data_core.schedules import ScheduleControls
    from qtrade_data_http.client import StaticScopeAuthorizer

    writes = []
    value = draft()
    store = SimpleNamespace(create=lambda *args: writes.append(args))
    controls = ScheduleControls(StaticScopeAuthorizer({}), store)
    with pytest.raises(DataFailure, match="forbidden"):
        controls.create(value, datetime.now(UTC))
    assert not writes


def test_authorization_outage_does_not_pause_or_reserve_a_run():
    from types import SimpleNamespace

    from qtrade_data_core.ports import DataFailure
    from qtrade_data_core.schedules import ScheduleControls

    calls = []

    class OfflineAuthorizer:
        def require(self, *args):
            raise DataFailure("unavailable")

    store = SimpleNamespace(
        pause=lambda *args: calls.append("pause"),
        reserve_run=lambda *args: calls.append("reserve"),
    )
    lease = SimpleNamespace(record=SimpleNamespace(draft=draft()))
    with pytest.raises(DataFailure, match="unavailable"):
        ScheduleControls(OfflineAuthorizer(), store).reserve_authorized_run(
            lease, datetime.now(UTC)
        )
    assert calls == []


def test_enabling_administrator_cannot_substitute_for_frozen_actor_permission():
    from types import SimpleNamespace

    from qtrade_data_core.ports import DataFailure
    from qtrade_data_core.schedules import ScheduleControls
    from qtrade_data_http.client import StaticScopeAuthorizer

    value, admin, writes = draft(), uuid4(), []
    record = SimpleNamespace(draft=value)
    store = SimpleNamespace(
        get=lambda *args: record,
        control=lambda *args: writes.append(args) or record,
    )
    auth = StaticScopeAuthorizer(
        {
            f"{admin}:{value.scope_id}": "manage",
            f"{value.actor_id}:{value.scope_id}": "read",
        }
    )
    controls = ScheduleControls(auth, store)
    with pytest.raises(DataFailure, match="forbidden"):
        controls.enable(admin, value.scope_id, value.schedule_id, value.revision)
    assert writes == []
    auth.grants[f"{value.actor_id}:{value.scope_id}"] = "manage"
    assert controls.enable(admin, value.scope_id, value.schedule_id, value.revision) == record
