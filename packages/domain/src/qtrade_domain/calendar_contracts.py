"""Map portable calendar documents to immutable domain schedules."""

from qtrade_contracts import CalendarOverride, CalendarSchedule, CalendarSession
from qtrade_domain.calendars import CalendarSnapshot, DayOverride, TradingCalendar, TradingSession


def from_contract(document: CalendarSchedule) -> CalendarSnapshot:
    def session(value: CalendarSession) -> TradingSession:
        return TradingSession(
            value.opens, value.closes, value.close_day_offset, value.open_fold, value.close_fold
        )

    return CalendarSnapshot(
        document.workspace_id,
        TradingCalendar(
            document.calendar_id,
            document.calendar_version,
            document.timezone,
            document.effective_from,
            document.effective_to,
            frozenset(document.weekdays),
            tuple(session(value) for value in document.sessions),
            tuple(
                DayOverride(value.trading_date, tuple(session(item) for item in value.sessions))
                for value in document.overrides
            ),
        ),
    )


def to_contract(snapshot: CalendarSnapshot) -> CalendarSchedule:
    def session(value: TradingSession) -> CalendarSession:
        return CalendarSession(
            opens=value.opens,
            closes=value.closes,
            close_day_offset=value.close_day_offset,
            open_fold=value.open_fold,
            close_fold=value.close_fold,
        )

    calendar = snapshot.calendar
    return CalendarSchedule(
        schema_version=1,
        workspace_id=snapshot.workspace_id,
        calendar_id=calendar.calendar_id,
        calendar_version=calendar.version,
        timezone=calendar.timezone,
        effective_from=calendar.effective_from,
        effective_to=calendar.effective_to,
        weekdays=tuple(sorted(calendar.weekdays)),
        sessions=tuple(session(value) for value in calendar.sessions),
        overrides=tuple(
            CalendarOverride(
                trading_date=value.trading_date,
                sessions=tuple(session(item) for item in value.sessions),
            )
            for value in calendar.overrides
        ),
    )
