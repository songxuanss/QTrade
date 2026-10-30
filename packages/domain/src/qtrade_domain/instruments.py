"""Immutable instrument imports and explicit specification-conflict facts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from qtrade_contracts import Instrument
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class InstrumentQuery:
    workspace_id: UUID
    instrument_id: str | None = None
    name: str | None = None
    exchange: str | None = None
    market: str | None = None
    enabled: bool | None = True
    limit: int = 50
    after: str | None = None

    def __post_init__(self) -> None:
        if type(self.limit) is not int or not 1 <= self.limit <= 200:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid instrument page size")
        if self.enabled is not None and type(self.enabled) is not bool:
            raise PortError(ErrorCode.INVALID_INPUT, "invalid instrument state filter")
        if any(
            value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 256)
            for value in (self.instrument_id, self.name, self.exchange, self.market, self.after)
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid instrument filter")


@dataclass(frozen=True)
class InstrumentPage:
    instruments: tuple[Instrument, ...]
    next_after: str | None


@dataclass(frozen=True)
class SpecificationConflict:
    issue_id: UUID
    workspace_id: UUID
    instrument_id: str
    existing: Instrument
    proposed: Instrument
    detected_at: datetime
    status: Literal["pending"] = "pending"


@dataclass(frozen=True)
class InstrumentImport:
    instrument_id: str
    status: Literal["created", "unchanged", "conflict"]
    issue_id: UUID | None = None


class InstrumentStorePort(Protocol):
    def import_instruments(
        self,
        actor_id: UUID,
        workspace_id: UUID,
        instruments: tuple[Instrument, ...],
    ) -> tuple[InstrumentImport, ...]: ...

    def query(self, query: InstrumentQuery) -> InstrumentPage: ...

    def conflicts(self, workspace_id: UUID) -> tuple[SpecificationConflict, ...]: ...


def validate_import(workspace_id: UUID, instruments: tuple[Instrument, ...]) -> None:
    if not isinstance(instruments, tuple) or not 1 <= len(instruments) <= 200:
        raise PortError(ErrorCode.INVALID_INPUT, "invalid instrument import size")
    ids = [item.instrument_id for item in instruments]
    if len(set(ids)) != len(ids) or any(item.workspace_id != workspace_id for item in instruments):
        raise PortError(ErrorCode.INVALID_INPUT, "instrument import scope or identifier mismatch")
    for item in instruments:
        for amount, precision in (
            (item.tick_size, item.price_precision),
            (item.lot_size, item.quantity_precision),
        ):
            components = amount.as_tuple()
            if not isinstance(components.exponent, int) or amount <= 0:
                raise PortError(ErrorCode.INVALID_INPUT, "invalid instrument increment")
            fractional_digits = -components.exponent - precision
            if fractional_digits > 0 and any(
                components.digits[max(0, len(components.digits) - fractional_digits) :]
            ):
                raise PortError(
                    ErrorCode.INVALID_INPUT, "instrument precision conflicts with increment"
                )
