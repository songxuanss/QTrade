"""Immutable incident transitions for consumers outside the collection module."""

from datetime import UTC
from typing import Literal, Self
from uuid import UUID, uuid5

from pydantic import AwareDatetime, model_validator

from .collection import digest
from .freshness import FreshnessResult
from .models import DataContract


class FreshnessNotice(DataContract):
    actor_id: UUID
    notice_id: UUID
    episode_id: UUID
    kind: Literal["opened", "recovered"]
    opened_at: AwareDatetime
    occurred_at: AwareDatetime
    result: FreshnessResult

    @model_validator(mode="after")
    def consistent_transition(self) -> Self:
        key_hash = digest(self.result.key.model_dump(mode="json"))
        episode = uuid5(
            self.scope_id,
            f"freshness-episode:{key_hash}:{self.opened_at.astimezone(UTC).isoformat()}",
        )
        if (
            self.scope_id != self.result.scope_id
            or self.occurred_at != self.result.checked_at
            or self.opened_at > self.occurred_at
            or self.episode_id != episode
            or self.notice_id != uuid5(episode, self.kind)
        ):
            raise ValueError("notice identity or clock mismatch")
        if self.kind == "opened":
            if self.opened_at != self.occurred_at or self.result.status not in {
                "late",
                "unavailable",
            }:
                raise ValueError("incident requires an overdue or unavailable observation")
        elif (
            self.opened_at >= self.occurred_at
            or self.result.status != "current"
            or self.result.evidence.source_health != "available"
        ):
            raise ValueError("recovery requires later current data and an available source")
        return self
