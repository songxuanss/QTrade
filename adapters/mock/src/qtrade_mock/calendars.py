"""Immutable workspace calendar snapshots; no assumed external exchange schedule."""

from threading import RLock
from uuid import UUID

from qtrade_domain.calendars import CalendarSnapshot
from qtrade_domain.ports import ErrorCode, PortError


class MockCalendarStore:
    def __init__(self) -> None:
        self._snapshots: dict[tuple[UUID, str, int], CalendarSnapshot] = {}
        self._lock = RLock()

    def put(self, actor_id: UUID, snapshot: CalendarSnapshot) -> None:
        key = (snapshot.workspace_id, snapshot.calendar.calendar_id, snapshot.calendar.version)
        with self._lock:
            previous = self._snapshots.get(key)
            if previous is not None and previous != snapshot:
                raise PortError(ErrorCode.CONFLICT, "calendar version is immutable")
            self._snapshots[key] = snapshot

    def get(
        self, workspace_id: UUID, calendar_id: str, version: int | None = None
    ) -> CalendarSnapshot:
        with self._lock:
            candidates = [
                value
                for (workspace, identifier, revision), value in self._snapshots.items()
                if workspace == workspace_id
                and identifier == calendar_id
                and (version is None or revision == version)
            ]
            if not candidates:
                raise PortError(ErrorCode.NOT_FOUND, "calendar unavailable")
            return max(candidates, key=lambda value: value.calendar.version)
