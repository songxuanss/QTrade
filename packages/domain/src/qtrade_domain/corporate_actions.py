"""Apply frozen corporate actions to a backtest cash/position ledger."""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal

from qtrade_data_contracts.research import CorporateAction
from qtrade_domain.ports import ErrorCode, PortError


@dataclass(frozen=True, slots=True)
class CorporateActionResult:
    quantity: Decimal
    available: Decimal
    cash: Decimal
    applied: tuple[CorporateAction, ...]


@dataclass(slots=True)
class CorporateActionLedger:
    """Idempotent ex-date ledger.

    Cash is credited from ex-date holdings before that day's fills. Bonus and
    transfer shares increase total quantity but not same-day sellable quantity;
    splits scale both. Rights are rejected rather than guessed.
    """

    actions: tuple[CorporateAction, ...]
    _by_instrument: dict[str, list[CorporateAction]] = field(init=False)
    _applied: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        grouped: dict[str, list[CorporateAction]] = {}
        for action in self.actions:
            if not isinstance(action, CorporateAction):
                raise PortError(ErrorCode.INVALID_INPUT, "invalid corporate action")
            if action.action == "rights":
                raise PortError(ErrorCode.UNSUPPORTED, "rights actions are not modeled")
            if action.ex_date is None:
                raise PortError(ErrorCode.UNSUPPORTED, "corporate action requires an ex-date")
            grouped.setdefault(action.instrument_id, []).append(action)
        for values in grouped.values():
            values.sort(key=lambda item: (item.ex_date, item.event_identity))
        self._by_instrument = grouped

    def apply(
        self,
        instrument_id: str,
        market_date: date,
        quantity: Decimal,
        available: Decimal,
        cash: Decimal,
        currency: str,
    ) -> CorporateActionResult:
        applied: list[CorporateAction] = []
        # Cash entitlement is fixed at the start of the ex-date. Applying a
        # bonus/split first must not inflate the dividend for the same day.
        entitlement = quantity
        for action in self._by_instrument.get(instrument_id, ()):
            if action.event_identity in self._applied:
                continue
            assert action.ex_date is not None
            if market_date < action.ex_date:
                continue
            if action.cash is not None:
                if action.cash_currency != currency:
                    raise PortError(
                        ErrorCode.UNSUPPORTED,
                        "corporate-action cash currency differs from the run",
                    )
                cash += entitlement / action.basis_shares * action.cash
            if action.bonus_shares is not None or action.transfer_shares is not None:
                extra = (
                    quantity
                    / action.basis_shares
                    * ((action.bonus_shares or Decimal(0)) + (action.transfer_shares or Decimal(0)))
                ).to_integral_value(rounding=ROUND_DOWN)
                quantity += extra
            if action.split_ratio is not None:
                quantity = (quantity * action.split_ratio).to_integral_value(rounding=ROUND_DOWN)
                available = (available * action.split_ratio).to_integral_value(rounding=ROUND_DOWN)
            self._applied.add(action.event_identity)
            applied.append(action)
        return CorporateActionResult(quantity, available, cash, tuple(applied))
