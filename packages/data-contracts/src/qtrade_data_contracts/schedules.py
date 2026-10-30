"""Frozen automatic collection inputs; credentials never belong to a schedule."""

from datetime import time
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, model_validator

from .collection import CollectionPeriod, check_instrument, digest
from .models import DataContract, Digest, Text
from .research import StrictRecord
from .research_transport import DateAxis, ResearchKind


class ScheduleTargets(StrictRecord):
    source: Text
    instrument_ids: Annotated[tuple[Text, ...], Field(min_length=1, max_length=10000)]
    membership_snapshot_id: UUID | None = None
    membership_content_sha256: Digest | None = None

    @model_validator(mode="after")
    def frozen_targets(self) -> Self:
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("duplicate schedule instrument")
        for identifier in self.instrument_ids:
            check_instrument(identifier)
        if (self.membership_snapshot_id is None) != (self.membership_content_sha256 is None):
            raise ValueError("membership reference requires identity and content version")
        return self


class CollectionScheduleControl(StrictRecord):
    revision: Digest


class MarketScheduleSelection(ScheduleTargets):
    kind: Literal["market"]
    dataset_kind: Literal["bars", "factors"]
    granularity: CollectionPeriod
    fields: Annotated[tuple[Text, ...], Field(max_length=200)] = ()

    @model_validator(mode="after")
    def selected_fields(self) -> Self:
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("duplicate schedule field")
        if self.dataset_kind == "factors" and (self.granularity != "1d" or not self.fields):
            raise ValueError("factor schedule requires explicit daily fields")
        if self.dataset_kind == "bars" and self.fields:
            raise ValueError("bar schedule cannot select factor fields")
        return self


class ResearchScheduleSelection(ScheduleTargets):
    kind: Literal["research"]
    dataset_kind: ResearchKind
    date_axis: DateAxis


class CollectionScheduleDraft(DataContract):
    schedule_id: UUID
    actor_id: UUID
    name: Text
    selection: Annotated[
        MarketScheduleSelection | ResearchScheduleSelection, Field(discriminator="kind")
    ]
    cadence: Literal["daily", "weekly", "quarterly"]
    timezone: Text
    local_time: time
    weekday: Annotated[int, Field(ge=0, le=6)] = 4
    lookback_days: Annotated[int, Field(ge=1, le=3660)] = 366
    catchup_days: Annotated[int, Field(ge=1, le=31)] = 7
    approved_parts: Annotated[int, Field(ge=1, le=1000000)]
    approved_credits: Annotated[int, Field(ge=1, le=1000000000)]
    automation_confirmed: bool

    @model_validator(mode="after")
    def explicit_automation(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unsupported schedule timezone") from None
        if (
            self.local_time.tzinfo is not None
            or self.local_time.second
            or self.local_time.microsecond
        ):
            raise ValueError("schedule local time must be minute precision without timezone")
        if self.automation_confirmed is not True:
            raise ValueError("explicit automation acknowledgement required")
        return self

    @property
    def revision(self) -> str:
        # Frozen members and approval caps participate in identity. A cadence is
        # operator intent, never evidence of the provider's publication rhythm.
        return digest(self.model_dump(mode="json"))


class CollectionScheduleRecord(DataContract):
    draft: CollectionScheduleDraft
    revision: Digest
    state: Literal["disabled", "enabled", "paused"]
    next_due_at: AwareDatetime
    updated_at: AwareDatetime
    pause_reason: Literal["authorization", "source_unavailable", "approval_required"] | None = None

    @model_validator(mode="after")
    def coherent_record(self) -> Self:
        if self.scope_id != self.draft.scope_id or self.revision != self.draft.revision:
            raise ValueError("schedule identity/version mismatch")
        if (self.state == "paused") != (self.pause_reason is not None):
            raise ValueError("pause reason must match paused state")
        return self
