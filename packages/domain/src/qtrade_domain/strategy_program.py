"""Strategy callbacks produce proposals; only the application authorizes orders."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from qtrade_contracts import Bar
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True)
class StrategySignal:
    instrument_id: str
    side: Literal["buy", "sell"]
    quantity: Decimal
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "market"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.instrument_id, str)
            or not 1 <= len(self.instrument_id) <= 256
            or not isinstance(self.side, str)
            or self.side not in {"buy", "sell"}
            or not isinstance(self.order_type, str)
            or self.order_type not in {"market", "limit", "stop", "stop_limit"}
            or not isinstance(self.time_in_force, str)
            or self.time_in_force not in {"day", "gtc", "ioc", "fok"}
            or not isinstance(self.quantity, Decimal)
            or not self.quantity.is_finite()
            or self.quantity <= 0
            or not _bounded_decimal(self.quantity)
            or any(
                v is not None
                and (
                    not isinstance(v, Decimal)
                    or not v.is_finite()
                    or v <= 0
                    or not _bounded_decimal(v)
                )
                for v in (self.limit_price, self.stop_price)
            )
            or (self.limit_price is not None) != (self.order_type in {"limit", "stop_limit"})
            or (self.stop_price is not None) != (self.order_type in {"stop", "stop_limit"})
        ):
            raise PortError(ErrorCode.INVALID_INPUT, "invalid strategy order proposal")


def _bounded_decimal(value: Decimal) -> bool:
    # Keep untrusted proposals within the canonical Decimal arithmetic budget.
    parts = value.as_tuple()
    return (
        isinstance(parts.exponent, int)
        and parts.exponent >= -128
        and value.adjusted() <= 27
        and len(parts.digits) <= 28
    )


class StrategyProgramPort(Protocol):
    """Replay an immutable history prefix and return proposals on its last bar.

    History is complete from runtime start, ordered and causal; callers never
    provide future bars. Each evaluation reconstructs strategy state after a
    process restart. Callers persist generated Intents before broker dispatch.
    Implementations must execute source only inside the isolated Runner.
    """

    def evaluate(
        self, code: str, history: tuple[Bar, ...], parameters: dict[str, object]
    ) -> tuple[StrategySignal, ...]: ...
