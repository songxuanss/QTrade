"""Persistent, explicitly enabled checks; watches never imply price downloads."""

from typing import Annotated, Self
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

from .collection import digest
from .freshness import FreshnessKey
from .models import DataContract, ErrorCode

Interval = Annotated[int, Field(ge=60, le=86400)]
Revision = Annotated[int, Field(ge=1)]


def watch_identity(key: FreshnessKey) -> UUID:
    return uuid5(key.scope_id, "freshness-watch:" + digest(key.model_dump(mode="json")))


class FreshnessWatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    publication_id: UUID
    interval_seconds: Interval = 900


class FreshnessWatchControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    revision: Revision
    enabled: StrictBool


class FreshnessWatch(DataContract):
    watch_id: UUID
    actor_id: UUID
    key: FreshnessKey
    reference_publication_id: UUID
    revision: Revision
    enabled: StrictBool
    interval_seconds: Interval
    created_at: AwareDatetime
    updated_at: AwareDatetime
    next_due_at: AwareDatetime
    last_checked_at: AwareDatetime | None = None
    last_attempt_at: AwareDatetime | None = None
    last_error_code: ErrorCode | None = None
    paused_reason: ErrorCode | None = None

    @model_validator(mode="after")
    def consistent_watch(self) -> Self:
        if (
            self.scope_id != self.key.scope_id
            or self.updated_at < self.created_at
            or self.watch_id != watch_identity(self.key)
        ):
            raise ValueError("watch scope or revision clock mismatch")
        if self.enabled and self.paused_reason is not None:
            raise ValueError("paused watch cannot execute")
        if self.last_checked_at is not None and self.last_checked_at > self.updated_at:
            raise ValueError("check cannot follow watch update")
        if self.last_error_code is not None and self.last_attempt_at is None:
            raise ValueError("failed attempt needs its own observation time")
        if self.last_attempt_at is not None and self.last_attempt_at > self.updated_at:
            raise ValueError("attempt cannot follow watch update")
        return self
