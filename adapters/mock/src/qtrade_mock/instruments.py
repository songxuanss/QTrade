"""Instrument master double retaining historical specifications and conflicts."""

from datetime import UTC, datetime
from threading import RLock
from uuid import UUID, uuid4

from qtrade_contracts import Instrument
from qtrade_domain.instruments import (
    InstrumentImport,
    InstrumentPage,
    InstrumentQuery,
    SpecificationConflict,
    validate_import,
)


class MockInstrumentStore:
    def __init__(self) -> None:
        self._items: dict[tuple[UUID, str], Instrument] = {}
        self._conflicts: dict[tuple[UUID, str, Instrument], SpecificationConflict] = {}
        self._lock = RLock()

    def import_instruments(
        self,
        actor_id: UUID,
        workspace_id: UUID,
        instruments: tuple[Instrument, ...],
    ) -> tuple[InstrumentImport, ...]:
        validate_import(workspace_id, instruments)
        with self._lock:
            result = []
            for item in instruments:
                key = (workspace_id, item.instrument_id)
                previous = self._items.get(key)
                if previous is None:
                    self._items[key] = item
                    result.append(InstrumentImport(item.instrument_id, "created"))
                elif previous == item:
                    result.append(InstrumentImport(item.instrument_id, "unchanged"))
                else:
                    conflict_key = (*key, item)
                    conflict = self._conflicts.setdefault(
                        conflict_key,
                        SpecificationConflict(
                            uuid4(),
                            workspace_id,
                            item.instrument_id,
                            previous,
                            item,
                            datetime.now(UTC),
                        ),
                    )
                    result.append(
                        InstrumentImport(item.instrument_id, "conflict", conflict.issue_id)
                    )
            return tuple(result)

    def query(self, query: InstrumentQuery) -> InstrumentPage:
        with self._lock:
            items = sorted(
                (
                    item
                    for (workspace_id, _), item in self._items.items()
                    if workspace_id == query.workspace_id
                ),
                key=lambda item: item.instrument_id,
            )
            items = [
                item
                for item in items
                if (query.instrument_id is None or item.instrument_id == query.instrument_id)
                and (query.name is None or query.name.casefold() in item.name.casefold())
                and (query.exchange is None or item.exchange == query.exchange)
                and (query.market is None or item.market == query.market)
                and (query.enabled is None or item.enabled == query.enabled)
                and (query.after is None or item.instrument_id > query.after)
            ]
            page = tuple(items[: query.limit])
            return InstrumentPage(
                page, page[-1].instrument_id if len(items) > query.limit else None
            )

    def conflicts(self, workspace_id: UUID) -> tuple[SpecificationConflict, ...]:
        with self._lock:
            return tuple(
                item for item in self._conflicts.values() if item.workspace_id == workspace_id
            )
