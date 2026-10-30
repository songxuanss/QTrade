"""Backtest run lifecycle records and persistence seam."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrade_contracts import BacktestRun
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True, slots=True)
class BacktestRunRecord:
    run: BacktestRun
    engine_job_id: UUID | None
    revision: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.run, BacktestRun)
            or (self.engine_job_id is not None and not isinstance(self.engine_job_id, UUID))
            or type(self.revision) is not int
            or self.revision < 1
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest run record")


@dataclass(frozen=True, slots=True)
class BacktestRunEvent:
    event_id: UUID
    workspace_id: UUID
    run_id: UUID
    status: str
    progress: int
    occurred_at: datetime
    error_code: str | None = None
    revision: int = 1

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(item, UUID)
                for item in (self.event_id, self.workspace_id, self.run_id)
            )
            or self.status not in {"queued", "running", "cancelled", "failed", "completed"}
            or type(self.progress) is not int
            or not 0 <= self.progress <= 100
            or not isinstance(self.occurred_at, datetime)
            or self.occurred_at.utcoffset() is None
            or type(self.revision) is not int
            or self.revision < 1
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid backtest run event")


class BacktestRunPort(Protocol):
    """CAS-updated run state with an append-only progress and terminal history."""

    def create(self, record: BacktestRunRecord, event: BacktestRunEvent) -> None: ...

    def get(self, workspace_id: UUID, run_id: UUID) -> BacktestRunRecord: ...

    def list(self, workspace_id: UUID, limit: int = 100) -> tuple[BacktestRunRecord, ...]: ...

    def history(self, workspace_id: UUID, run_id: UUID) -> tuple[BacktestRunEvent, ...]: ...

    def update(
        self,
        expected_revision: int,
        record: BacktestRunRecord,
        event: BacktestRunEvent | None,
    ) -> BacktestRunRecord: ...
