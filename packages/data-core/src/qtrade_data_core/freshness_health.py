"""Read health without supplier I/O, mutating watches, or acknowledging deliveries."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrade_data_contracts.collection import SourceDataset, digest
from qtrade_data_contracts.freshness import FreshnessKey, FreshnessResult
from qtrade_data_contracts.freshness_health import (
    ConsumerState,
    FreshnessHealthEntry,
    FreshnessHealthPage,
    FreshnessHealthQuery,
    SourceConfiguration,
    status_at,
)
from qtrade_data_contracts.freshness_notice import FreshnessNotice
from qtrade_data_contracts.freshness_watch import FreshnessWatch
from qtrade_data_core.ports import DataAuthorizer, DataFailure


@dataclass(frozen=True)
class HealthRecord:
    key: FreshnessKey
    instrument_name: str
    snapshot: FreshnessResult | None
    watch: FreshnessWatch | None
    last_notice: FreshnessNotice | None
    consumer_state: ConsumerState
    consumer_lease_expired: bool = False


@dataclass(frozen=True)
class HealthBatch:
    read_at: datetime
    records: tuple[HealthRecord, ...]
    next_key: str | None


class FreshnessHealthRepository(Protocol):
    def page(self, query: FreshnessHealthQuery, consumer: str) -> HealthBatch: ...


class FreshnessHealth:
    def __init__(
        self,
        auth: DataAuthorizer,
        repository: FreshnessHealthRepository,
        catalog: Callable[[], tuple[SourceDataset, ...]],
        consumer: str,
        max_age: int = 900,
    ):
        if type(max_age) is not int or not 1 <= max_age <= 86400:
            raise ValueError("bounded health snapshot age required")
        self.auth, self.repository, self.catalog = auth, repository, catalog
        self.consumer, self.max_age = consumer, max_age

    def read(self, actor: UUID, query: FreshnessHealthQuery) -> FreshnessHealthPage:
        """Authorize every page and validate its stored evidence at the DB read clock.

        This is an observation API: no provider probes, watch controls, notice
        claims, or acknowledgements are allowed through this boundary.
        """
        self.auth.require(actor, query.scope_id, "read")
        try:
            query = FreshnessHealthQuery.model_validate_json(query.model_dump_json())
        except ValueError:
            raise DataFailure("invalid_input") from None
        batch = self.repository.page(query, self.consumer)
        if batch.read_at.utcoffset() is None or len(batch.records) > query.limit:
            raise DataFailure("unverified")
        # Catalog is the local registered configuration, not a permission probe.
        # Unknown/unconfigured sources cannot inherit a green fixture snapshot.
        configured: dict[tuple[str, str, str], bool] = {}
        try:
            for item in self.catalog():
                item = SourceDataset.model_validate_json(item.model_dump_json())
                configured[(item.source, item.dataset_kind, item.mapping_version)] = item.configured
        except ValueError:
            raise DataFailure("unverified") from None
        entries = []
        for record in batch.records:
            config = configured.get(
                (record.key.source, record.key.dataset_kind, record.key.mapping_version)
            )
            source: SourceConfiguration = (
                "unknown" if config is None else "configured" if config else "unconfigured"
            )
            try:
                entries.append(
                    FreshnessHealthEntry(
                        key_sha256=digest(record.key.model_dump(mode="json")),
                        key=record.key,
                        instrument_name=record.instrument_name,
                        snapshot=record.snapshot,
                        watch=record.watch,
                        last_notice=record.last_notice,
                        consumer_state=record.consumer_state,
                        consumer_lease_expired=record.consumer_lease_expired,
                        source_configuration=source,
                        effective_status=status_at(
                            record.snapshot, record.watch, source, batch.read_at, self.max_age
                        ),
                        age_seconds=max(
                            0, int((batch.read_at - record.snapshot.checked_at).total_seconds())
                        )
                        if record.snapshot
                        else None,
                    )
                )
            except ValueError:
                raise DataFailure("unverified") from None
        try:
            page = FreshnessHealthPage(
                schema_version=1,
                scope_id=query.scope_id,
                read_at=batch.read_at,
                max_age_seconds=self.max_age,
                consumer_id=self.consumer,
                entries=tuple(entries),
                next_key=batch.next_key,
            )
        except ValueError:
            raise DataFailure("unverified") from None
        if (
            query.after_key is not None
            and any(e.key_sha256 <= query.after_key for e in page.entries)
        ) or (page.next_key is not None and len(page.entries) != query.limit):
            raise DataFailure("unverified")
        return page
