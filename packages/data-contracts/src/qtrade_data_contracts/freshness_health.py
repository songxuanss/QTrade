"""Scoped, bounded health reads; saved checks are not live supplier probes."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .collection import digest
from .freshness import FreshnessKey, FreshnessResult
from .freshness_notice import FreshnessNotice
from .freshness_watch import FreshnessWatch
from .models import DataContract, Digest, Text

HealthStatus = Literal[
    "current",
    "within_grace",
    "late",
    "unknown",
    "unavailable",
    "unchecked",
    "expired",
    "check_failed",
    "clock_skew",
    "source_unconfigured",
]
SourceConfiguration = Literal["configured", "unconfigured", "unknown"]
ConsumerState = Literal["not_applicable", "not_claimed", "pending", "leased", "delivered"]


class FreshnessHealthQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    scope_id: UUID
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    after_key: Digest | None = None


class FreshnessHealthEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    key_sha256: Digest
    key: FreshnessKey
    instrument_name: Text
    snapshot: FreshnessResult | None
    watch: FreshnessWatch | None
    last_notice: FreshnessNotice | None
    consumer_state: ConsumerState
    consumer_lease_expired: bool = False
    source_configuration: SourceConfiguration
    effective_status: HealthStatus
    age_seconds: Annotated[int, Field(ge=0)] | None

    @model_validator(mode="after")
    def bound_evidence(self) -> Self:
        if self.key_sha256 != digest(self.key.model_dump(mode="json")):
            raise ValueError("health identity digest mismatch")
        if self.snapshot is None and self.watch is None:
            raise ValueError("health inventory requires a saved check or watch")
        if self.snapshot is not None and self.snapshot.key != self.key:
            raise ValueError("health snapshot mismatch")
        if self.watch is not None and self.watch.key != self.key:
            raise ValueError("health watch mismatch")
        if self.last_notice is not None and self.last_notice.result.key != self.key:
            raise ValueError("health notice mismatch")
        if (self.last_notice is None) != (self.consumer_state == "not_applicable"):
            raise ValueError("consumer status needs a notice")
        if self.consumer_lease_expired and self.consumer_state != "leased":
            raise ValueError("only a leased delivery can expire")
        return self


def status_at(
    snapshot: FreshnessResult | None,
    watch: FreshnessWatch | None,
    configured: SourceConfiguration,
    read_at: AwareDatetime,
    max_age: int,
) -> HealthStatus:
    """Temporal consistency of a read, without reinterpreting vendor rules.

    Current/late/grace refer to the last saved check. Expiry or a later failed
    attempt removes any present-tense assurance; config edits cannot retime an
    old failure. Registered configuration alone never proves source permission.
    """
    if (snapshot and snapshot.checked_at > read_at) or (
        watch and watch.last_attempt_at and watch.last_attempt_at > read_at
    ):
        return "clock_skew"
    if (
        watch
        and watch.last_error_code
        and watch.last_attempt_at
        and (snapshot is None or watch.last_attempt_at >= snapshot.checked_at)
    ):
        return "check_failed"
    if configured != "configured":
        return "source_unconfigured"
    if snapshot is None:
        return "unchecked"
    if (read_at - snapshot.checked_at).total_seconds() > max_age:
        return "expired"
    return snapshot.status


class FreshnessHealthPage(DataContract):
    read_at: AwareDatetime
    max_age_seconds: Annotated[int, Field(ge=1, le=86400)]
    consumer_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
    entries: Annotated[tuple[FreshnessHealthEntry, ...], Field(max_length=100)]
    next_key: Digest | None

    @model_validator(mode="after")
    def consistent_page(self) -> Self:
        hashes = [row.key_sha256 for row in self.entries]
        if hashes != sorted(set(hashes)):
            raise ValueError("health inventory must be unique and ordered")
        if self.next_key is not None and (not hashes or self.next_key != hashes[-1]):
            raise ValueError("health cursor must identify the last returned key")
        for row in self.entries:
            if row.key.scope_id != self.scope_id:
                raise ValueError("health scope mismatch")
            age = (
                max(0, int((self.read_at - row.snapshot.checked_at).total_seconds()))
                if row.snapshot
                else None
            )
            if row.age_seconds != age or row.effective_status != status_at(
                row.snapshot,
                row.watch,
                row.source_configuration,
                self.read_at,
                self.max_age_seconds,
            ):
                raise ValueError("health status disagrees with saved evidence and read clock")
        return self
