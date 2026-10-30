"""Source capability is configuration, not proof of completeness or health."""

from dataclasses import dataclass
from datetime import datetime

from qtrade_contracts.models import Granularity
from qtrade_domain.ingestion import CollectionRequest
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class HistoricalSource:
    source: str
    authorization_id: str
    granularities: tuple[Granularity, ...]
    earliest: datetime | None = None

    def validate(self, request: CollectionRequest) -> None:
        if request.source != self.source or request.authorization_id != self.authorization_id:
            raise PortError(ErrorCode.UNSUPPORTED, "unsupported source authorization")
        if request.granularity not in self.granularities:
            raise PortError(ErrorCode.UNSUPPORTED, "period is not enabled for source")
        if self.earliest is not None and request.start < self.earliest:
            raise PortError(ErrorCode.INVALID_INPUT, "request predates source archive")
