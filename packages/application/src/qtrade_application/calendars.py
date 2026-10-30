"""Market state uses explicit versioned schedules and authorized workspace scope."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from qtrade_contracts import Instrument
from qtrade_domain.authorization import Permission, WorkspaceAccess
from qtrade_domain.calendars import (
    CalendarSnapshot,
    CalendarStorePort,
    SessionWindow,
    TradingCalendar,
)
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class MarketState:
    calendar_id: str
    calendar_version: int
    timezone: str
    measured_at: datetime
    is_open: bool
    active_session: SessionWindow | None


class CalendarService:
    def __init__(self, store: CalendarStorePort):
        self._store = store

    def put(self, access: WorkspaceAccess, calendar: TradingCalendar) -> None:
        access.require(Permission.DATA_MANAGE)
        self._store.put(access.user_id, CalendarSnapshot(access.workspace.workspace_id, calendar))

    def _calendar(
        self, access: WorkspaceAccess, calendar_id: str, version: int | None
    ) -> TradingCalendar:
        access.require(Permission.READ)
        if (
            not isinstance(calendar_id, str)
            or not 1 <= len(calendar_id) <= 256
            or (version is not None and (type(version) is not int or version < 1))
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid calendar identity or version")
        snapshot = self._store.get(access.workspace.workspace_id, calendar_id, version)
        if (
            snapshot.workspace_id != access.workspace.workspace_id
            or snapshot.calendar.calendar_id != calendar_id
            or (version is not None and snapshot.calendar.version != version)
        ):
            raise PortError(
                ErrorCode.UNAVAILABLE, "calendar adapter returned invalid scope or version"
            )
        return snapshot.calendar

    def state(
        self,
        access: WorkspaceAccess,
        calendar_id: str,
        instant: datetime,
        version: int | None = None,
    ) -> MarketState:
        calendar = self._calendar(access, calendar_id, version)
        active = calendar.active_window(instant)
        return MarketState(
            calendar.calendar_id,
            calendar.version,
            calendar.timezone,
            instant.astimezone(UTC),
            active is not None,
            active,
        )

    def instrument_state(
        self,
        access: WorkspaceAccess,
        instrument: Instrument,
        instant: datetime,
        version: int | None = None,
    ) -> MarketState:
        access.require(Permission.READ, object_workspace_id=instrument.workspace_id)
        if not instrument.enabled:
            raise PortError(ErrorCode.REJECTED, "instrument is disabled")
        calendar = self._calendar(access, instrument.calendar_id, version)
        if calendar.timezone != instrument.timezone:
            raise PortError(ErrorCode.UNAVAILABLE, "instrument and calendar timezones disagree")
        active = calendar.active_window(instant)
        return MarketState(
            calendar.calendar_id,
            calendar.version,
            calendar.timezone,
            instant.astimezone(UTC),
            active is not None,
            active,
        )

    def sessions(
        self,
        access: WorkspaceAccess,
        calendar_id: str,
        start: date,
        end: date,
        version: int | None = None,
    ) -> tuple[SessionWindow, ...]:
        if type(start) is not date or type(end) is not date or not 1 <= (end - start).days <= 366:
            raise PortError(
                ErrorCode.INVALID_INPUT, "invalid calendar range; maximum 366 trading dates"
            )
        calendar = self._calendar(access, calendar_id, version)
        return tuple(
            window
            for offset in range((end - start).days)
            for window in calendar.windows(start + timedelta(days=offset))
        )
