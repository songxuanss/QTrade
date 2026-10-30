"""Application download range policy, independent of provider request batching."""

from dataclasses import dataclass
from datetime import datetime

from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True, slots=True)
class DownloadRangePolicy:
    max_days: int | None = None

    def __post_init__(self) -> None:
        if self.max_days is not None and (type(self.max_days) is not int or self.max_days <= 0):
            raise ValueError("download maximum must be a positive integer or unlimited")

    def exceeded(self, start: datetime, end: datetime) -> bool:
        return self.max_days is not None and (end - start).total_seconds() > self.max_days * 86400

    def validate(self, start: datetime, end: datetime) -> None:
        if self.exceeded(start, end):
            raise PortError(ErrorCode.INVALID_INPUT, "download range exceeds configured maximum")
