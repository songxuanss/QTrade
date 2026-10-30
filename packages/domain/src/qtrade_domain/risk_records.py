"""Append-only persistence contract for risk decisions and their input summaries."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qtrade_contracts import RiskDecision
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class RiskDecisionRecord:
    decision: RiskDecision
    input_summary_json: str

    def __post_init__(self) -> None:
        try:
            summary = json.loads(self.input_summary_json)
        except (TypeError, ValueError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid risk input summary") from None
        if not isinstance(summary, dict) or len(self.input_summary_json) > 32768:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid risk input summary")
        try:
            normalized = json.dumps(summary, separators=(",", ":"), sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid risk input summary") from None
        object.__setattr__(self, "input_summary_json", normalized)


class RiskDecisionStore(Protocol):
    def record(self, record: RiskDecisionRecord) -> RiskDecisionRecord: ...

    def get_for_intent(self, workspace_id: UUID, intent_id: UUID) -> RiskDecisionRecord | None: ...

    def list(
        self,
        workspace_id: UUID,
        *,
        outcome: str | None,
        since: datetime | None,
        limit: int,
    ) -> tuple[RiskDecisionRecord, ...]: ...
