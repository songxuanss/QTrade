"""Provider-declared temporal boundaries, independent of vendor names or storage."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TimePartition:
    timezone: str
    annual: bool = False
    max_days: int = 0

    def __post_init__(self) -> None:
        ZoneInfo(self.timezone)
        if type(self.max_days) is not int or self.max_days < 0:
            raise ValueError("partition days must be a nonnegative integer")

    def spans(self, start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("partition requires an aware nonempty interval")
        zone = ZoneInfo(self.timezone)
        cursor = start
        while cursor < end:
            right = end
            if self.annual:
                year = cursor.astimezone(zone).year
                right = min(right, datetime(year + 1, 1, 1, tzinfo=zone).astimezone(UTC))
            if self.max_days:
                right = min(right, cursor + timedelta(days=self.max_days))
            yield cursor, right
            cursor = right
