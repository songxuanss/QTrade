"""Bounded audit queries and safe report projections."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from qtrade_domain.authorization import WorkspaceAccess
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class AuditQuery:
    start: datetime
    end: datetime
    actor_id: UUID | None = None
    action: str | None = None
    target_id: UUID | None = None
    result: str | None = None
    trace_id: UUID | None = None
    before: int | None = None

    def __post_init__(self) -> None:
        if (
            self.start.utcoffset() is None
            or self.end.utcoffset() is None
            or not timedelta(0) < self.end - self.start <= timedelta(days=31)
            or self.result not in {None, "success", "denied", "failure"}
            or (self.before is not None and (type(self.before) is not int or self.before < 1))
            or (self.action is not None and not 1 <= len(self.action) <= 256)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid audit query range or filter")


class AuditQueryPort(Protocol):
    def query(
        self, access: WorkspaceAccess, query: AuditQuery, limit: int
    ) -> tuple[dict[str, object], ...]: ...
