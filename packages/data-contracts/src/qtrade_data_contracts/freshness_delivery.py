"""Bounded service-to-service incident delivery; no browser health assertions."""

from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .freshness_notice import FreshnessNotice
from .models import DataContract

Consumer = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]


class FreshnessNoticeClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    consumer_id: Consumer
    limit: Annotated[int, Field(ge=1, le=100)] = 20


class FreshnessNoticeLease(DataContract):
    consumer_id: Consumer
    notice: FreshnessNotice
    token: UUID
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def consistent_scope(self) -> Self:
        if self.scope_id != self.notice.scope_id:
            raise ValueError("delivery scope mismatch")
        return self


class FreshnessNoticeAck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    consumer_id: Consumer
    token: UUID
