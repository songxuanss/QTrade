"""Bounded operator cadence slots; exchange eligibility belongs to source rules."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from qtrade_data_contracts.schedules import CollectionScheduleDraft
from qtrade_data_core.ports import DataFailure


def aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataFailure("invalid_input")


def calendar_candidate(draft: CollectionScheduleDraft, day: date) -> datetime | None:
    if draft.cadence == "weekly" and day.weekday() != draft.weekday:
        return None
    if draft.cadence == "quarterly" and (day.day != 1 or day.month not in {1, 4, 7, 10}):
        return None
    zone = ZoneInfo(draft.timezone)
    local = datetime.combine(day, draft.local_time)
    # fold=0 chooses the first occurrence of repeated local time. A nonexistent
    # wall time is skipped rather than silently shifted into another time.
    candidate = local.replace(tzinfo=zone, fold=0).astimezone(UTC)
    if candidate.astimezone(zone).replace(tzinfo=None) != local:
        return None
    return candidate


def next_slot(draft: CollectionScheduleDraft, after: datetime) -> datetime:
    """First real cadence slot strictly after the supplied instant, always UTC."""
    aware(after)
    day = after.astimezone(ZoneInfo(draft.timezone)).date()
    for offset in range(367):
        candidate = calendar_candidate(draft, day + timedelta(days=offset))
        if candidate is not None and candidate > after:
            return candidate
    raise DataFailure("unavailable")


def previous_slot(draft: CollectionScheduleDraft, before: datetime) -> datetime:
    """Previous real cadence slot, including DST skip/fold rules, always UTC."""
    aware(before)
    day = before.astimezone(ZoneInfo(draft.timezone)).date()
    for offset in range(367):
        candidate = calendar_candidate(draft, day - timedelta(days=offset))
        if candidate is not None and candidate < before:
            return candidate
    raise DataFailure("unavailable")


@dataclass(frozen=True)
class DueSlots:
    slots: tuple[datetime, ...]
    next_due: datetime
    omitted_before: datetime | None


def due_slots(draft: CollectionScheduleDraft, cursor: datetime, now: datetime) -> DueSlots:
    """Recover only the configured rolling UTC horizon, never scan a long outage.

    A slot expresses operator intent. A holiday or unpublished sparse report
    must be handled by the provider policy before creating any download task.
    """
    aware(cursor)
    aware(now)
    if cursor > now:
        return DueSlots((), cursor.astimezone(UTC), None)
    cutoff = now.astimezone(UTC) - timedelta(days=draft.catchup_days)
    lower = max(cursor.astimezone(UTC), cutoff)
    zone = ZoneInfo(draft.timezone)
    first, last = lower.astimezone(zone).date(), now.astimezone(zone).date()
    # Bounded elapsed-day horizon can cross a DST transition; at most one extra
    # local date is required. Cursor remains inclusive to recover a crashed slot.
    if (last - first).days > draft.catchup_days + 1:
        raise DataFailure("unavailable")
    values = []
    for offset in range((last - first).days + 1):
        candidate = calendar_candidate(draft, first + timedelta(days=offset))
        if candidate is not None and lower <= candidate <= now:
            values.append(candidate)
    return DueSlots(tuple(values), next_slot(draft, now), cutoff if cursor < cutoff else None)
