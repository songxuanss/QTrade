"""Publication eligibility for daily prices; sparse categories need own policies."""

from datetime import datetime, time, timedelta
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from qtrade_data_contracts.collection import CollectionJobRequest
from qtrade_data_contracts.schedule_dispatch import MarketDispatchInput, ScheduleDispatchDecision
from qtrade_data_contracts.schedules import CollectionScheduleDraft, MarketScheduleSelection
from qtrade_data_core.default_range import DefaultRange
from qtrade_data_core.ports import DataFailure
from qtrade_data_core.schedule_clock import aware, previous_slot


class DailyPublicationProvider(Protocol):
    def default_range(self, source: str, granularity: str, now: datetime) -> DefaultRange: ...


class DailyPriceSchedulePolicy:
    def __init__(self, provider: DailyPublicationProvider):
        self.provider = provider

    def validate(self, draft: CollectionScheduleDraft, now: datetime) -> DefaultRange:
        selection = draft.selection
        if not isinstance(selection, MarketScheduleSelection) or (
            selection.dataset_kind != "bars" or selection.granularity != "1d"
        ):
            raise DataFailure("unsupported")
        return self.provider.default_range(selection.source, selection.granularity, now)

    def prepare(
        self, draft: CollectionScheduleDraft, run: UUID, due: datetime, now: datetime
    ) -> ScheduleDispatchDecision | None:
        aware(due)
        window = self.validate(draft, due)
        selection = draft.selection
        if not isinstance(selection, MarketScheduleSelection):
            raise DataFailure("unsupported")
        available = window.expected_available_at
        if available.tzinfo is None or available > due:
            raise DataFailure("unverified")
        if available <= previous_slot(draft, due):
            # No new expected publication between cadence slots: holiday or a
            # pre-release cadence is a normal skip, never a provider empty result.
            return None
        zone = ZoneInfo(window.timezone)
        end_day = window.last + timedelta(days=1)
        request = CollectionJobRequest(
            schema_version=1,
            scope_id=draft.scope_id,
            actor_id=draft.actor_id,
            request_id=run,
            correlation_id=run,
            source=selection.source,
            dataset_kind=selection.dataset_kind,
            instrument_ids=selection.instrument_ids,
            granularity=selection.granularity,
            fields=selection.fields,
            start=datetime.combine(end_day - timedelta(days=draft.lookback_days), time.min, zone),
            end=datetime.combine(end_day, time.min, zone),
            mode="missing",
        )
        return ScheduleDispatchDecision(
            schema_version=1,
            scope_id=draft.scope_id,
            schedule_id=draft.schedule_id,
            run_id=run,
            revision=draft.revision,
            due_at=due,
            frozen_at=now,
            dispatch=MarketDispatchInput(kind="market", request=request),
            policy_version="daily-price-publication.v1",
            basis=window.basis,
        )
